import os
import json
import httpx
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# 환경 변수 및 설정
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "openai/gpt-4o"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

class AI:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://class-twin.ai",
        }

    async def _call(self, messages: List[Dict], max_tokens: int = 1000) -> str:
        """OpenRouter API 호출"""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                API_URL,
                headers=self.headers,
                json={
                    "model": MODEL,
                    "max_tokens": max_tokens,
                    "messages": messages
                }
            )
            data = response.json()
            print(f"🔍 API 응답: {str(data)[:200]}")
            if "choices" not in data:
                print(f"❌ choices 없음. 전체 응답: {data}")
                raise Exception(f"API 오류: {data.get('error', data)}")
            return data["choices"][0]["message"]["content"]

    # ==================== 2단계: 조건 파싱 ====================

    async def parse_conditions(self, chat_input: str, student_names: List[str]) -> List[Dict]:
        """
        자연어 조건을 구조화된 데이터로 변환
        "민준이랑 서연이 분리해주세요" →
        [{"type": "분리", "student_a": "김민준", "student_b": "이서연"}]
        """
        if not chat_input.strip():
            return []

        messages = [
            {
                "role": "system",
                "content": f"""당신은 교사의 반배정 조건을 파싱하는 AI입니다.
교사가 자연어로 입력한 조건을 JSON 배열로 변환해주세요.

현재 학생 목록: {', '.join(student_names)}

출력 형식 (JSON만 출력, 다른 텍스트 없이):
[
  {{
    "type": "분리" | "같이 앉기" | "앞자리" | "교사 근처" | "기타",
    "student_a": "학생 이름 (학생 목록에서 정확히 매칭)",
    "student_b": "학생 이름 (해당시, 없으면 null)",
    "students": ["학생1", "학생2", "학생3"],
    "note": "원본 조건 설명"
  }}
]

규칙:
- 학생 이름 매칭: 성+이름 전체 우선, 성만 있으면 목록에서 가장 유사한 이름
- "분리", "떨어뜨려", "나눠", "따로", "다른 반" → type: "분리"
- "옆자리", "옆에", "같이", "함께", "같은 반", "붙여", "나란히" → type: "같이 앉기"
- "앞", "앞자리", "맨 앞", "칠판 앞" → type: "앞자리"
- "선생님 근처", "교탁 근처", "ADHD" → type: "교사 근처"
- "전학생 배려", "적응" → type: "기타" (note에 상세 설명)
- "형제", "자매", "남매" → 해당 학생 이름 찾아서 분리 조건
- 3명 이상이면 students 배열에 모두 포함
  예) "A B C 같은반" → {{"type":"같은 반","student_a":"A","student_b":"B","students":["A","B","C"]}}
- 이름이 없는 일반 조건 (전학생 배려 등) → student_a/b null, note에 설명
- 조건 없으면 빈 배열 []
- 반드시 JSON만 출력"""
            },
            {
                "role": "user",
                "content": chat_input
            }
        ]

        try:
            result = await self._call(messages, max_tokens=500)
            result = result.strip()
            # JSON 코드블록 제거
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:]
            parsed = json.loads(result)
            return parsed if isinstance(parsed, list) else []
        except Exception as e:
            print(f"조건 파싱 오류: {e}")
            return []

    # ==================== 챗봇 대화 ====================

    async def chat_response(self, message: str, history: List[Dict], students: List[Dict]) -> Dict:
        """챗봇 대화 응답 생성 (ai_traits 활용 + 스마트 질문 처리)"""
        
        # 학생 정보 요약 (이름 + ai_traits)
        student_info = []
        for s in students:
            info = s["name"]
            traits = s.get("ai_traits", {})
            if traits:
                trait_tags = []
                if traits.get("leadership", 0) >= 7:
                    trait_tags.append("리더십")
                if traits.get("sociability", 0) >= 7:
                    trait_tags.append("외향적")
                if traits.get("attention", 0) >= 7:
                    trait_tags.append("차분함")
                if traits.get("positive_influence", 0) >= 7:
                    trait_tags.append("긍정적")
                if trait_tags:
                    info += f"({','.join(trait_tags)})"
            student_info.append(info)
        
        student_names = [s["name"] for s in students]
        
        system_prompt = f"""당신은 반배정을 도와주는 AI 어시스턴트입니다.
교사의 질문과 요청을 구분하여 처리하세요.

현재 학생 정보: {', '.join(student_info)}

응답 유형 3가지:
1. **단순 질문** (정보 요청만): parsed_conditions = []
2. **애매한 조건** (의도 불명확): 확인 질문 + parsed_conditions = []
3. **명확한 조건** (즉시 적용 가능): parsed_conditions에 추가

응답 형식 (JSON):
{{
  "message": "교사에게 보낼 응답 메시지 (한국어, 친근하게)",
  "question_type": "simple_question|ambiguous|clear_condition",
  "parsed_conditions": [
    {{
      "type": "분리|같은 반|trait_balance|기타",
      "student_a": "학생명 또는 null",
      "student_b": "학생명 또는 null",
      "students": ["학생1", "학생2"],
      "trait": "leadership|sociability|attention|positive_influence (trait_balance일 때만)",
      "threshold": 7,
      "note": "조건 설명"
    }}
  ]
}}

규칙:
**단순 질문 처리 (question_type: "simple_question"):**
- "차분한 친구가 누구있지?", "외향적인 사람 누가 있어?", "리더 성향 학생 알려줘"
  → ai_traits 확인해서 해당 학생 이름 **모두** 나열
  → parsed_conditions = [] (조건 추가 안 함)
  → 예: "외향적인 학생은 이서연, 한지민, 신서경, 안건우, 류우지, 오소안, 전하윤, 장지유 등 총 15명이 있습니다."
  → 중요: 점수는 표시하지 말고 이름만 나열할 것. 해당하는 학생을 **모두** 나열.

**애매한 조건 처리 (question_type: "ambiguous"):**
- "A랑 B 사귄대", "A랑 B 친한가봐", "A가 B 좋아한대", "A랑 B 싸웠대"
  → 분리할지 같은 반 할지 불명확
  → parsed_conditions = [] (일단 추가 안 함)
  → 확인 질문: "A와 B를 같은 반에 배치하시겠어요, 아니면 분리하시겠어요?"
  → 중요: 단순히 정보를 전달하는 것이므로 조건 추가하지 말 것!

**명확한 조건 처리 (question_type: "clear_condition"):**
- 반드시 "~해줘", "~해주세요", "~하자", "~배치해", "~분산해" 같은 행동 요청 동사가 있어야 함
- "A랑 B 분리해줘", "A랑 B 같은 반으로 해줘", "외향적인 학생 분산해줘"
  → parsed_conditions에 즉시 추가
  → 예: {{"type": "분리", "student_a": "A", "student_b": "B"}}
  → 예: {{"type": "trait_balance", "trait": "sociability", "threshold": 7}}

**절대 조건 추가하면 안 되는 경우:**
- 단순 정보 전달: "A랑 B 사귄대", "A가 B 싫어한대", "A랑 B 친하대"
- 단순 질문: "~누구있지?", "~어때?", "~있어?"
- 일반 대화: "그렇구나", "알겠어", "확인했어"
- 행동 요청 동사가 없는 모든 발화

ai_traits 필드 매핑:
- leadership: 리더십, 반장 기질, 리더형
- sociability: 외향적, 사교적, 친화력, 교우관계 좋음
- attention: 차분한, 집중력 좋은, 성실한
- positive_influence: 긍정적인, 분위기 메이커, 밝은

추가 규칙:
- 특성 분산 요청 (외향적 골고루, 리더 균등 등) → type: "trait_balance", 해당 trait 필드 포함
- 학생 이름 언급 + 명확한 동사 → type: "분리" 또는 "같은 반"
- 친근하고 간결하게, 50자 이내
- 반드시 JSON 형식으로만 응답할 것. 다른 텍스트는 절대 포함하지 말 것.

출력 예시:
{{"message": "외향적인 학생은 이서연(사교성 9점), 한지민(사교성 8점)이 있습니다.", "question_type": "simple_question", "parsed_conditions": []}}"""

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-6:]:  # 최근 6개 대화만 유지
            messages.append(h)
        messages.append({"role": "user", "content": message})

        try:
            result = await self._call(messages, max_tokens=400)
            result = result.strip()
            
            # JSON 코드블록 제거
            if result.startswith("```"):
                lines = result.split("\n")
                result = "\n".join(lines[1:-1])  # 첫 줄과 마지막 줄 제거
                if result.startswith("json"):
                    result = result[4:].strip()
            
            # JSON 파싱
            parsed = json.loads(result)
            
            # 기본값 설정
            if "condition" in parsed and parsed["condition"] and "parsed_conditions" not in parsed:
                parsed["parsed_conditions"] = [parsed["condition"]]
            if "parsed_conditions" not in parsed:
                parsed["parsed_conditions"] = []
            if "question_type" not in parsed:
                parsed["question_type"] = "simple_question"
            if "message" not in parsed:
                parsed["message"] = "확인했습니다."
                
            return parsed
        except Exception as e:
            print(f"❌ 챗봇 파싱 실패: {e}")
            print(f"   원본 응답: {result[:300] if 'result' in locals() else 'N/A'}")
            
            # 일반 텍스트 응답인 경우 처리
            if 'result' in locals() and result:
                return {
                    "message": result[:200],  # 일반 텍스트를 메시지로 사용
                    "question_type": "simple_question",
                    "parsed_conditions": []
                }
            
            return {
                "message": "죄송합니다. 응답 처리 중 오류가 발생했습니다.",
                "parsed_conditions": [],
                "question_type": "simple_question"
            }

    # ==================== 5단계: 배치 이유 설명 ====================

    async def explain_assignment(self, assignment: Dict, students: List[Dict], relations: List[Dict]) -> Dict:
        """
        GPT-4o가 배치 결과의 이유를 자연어로 설명
        """
        student_info = []
        for s in students[:20]:  # 토큰 절약
            info = f"{s['name']}({s.get('gender','?')}/{s.get('academic_level','?')})"
            if s.get("special_needs"):
                info += f"[{s['special_needs']}]"
            student_info.append(info)

        conflict_info = [f"{r['student_a']}↔{r['student_b']}({r['type']})" for r in relations if r['type'] == '갈등']
        friend_info = [f"{r['student_a']}↔{r['student_b']}" for r in relations if r['type'] == '친함']

        classes = assignment.get("classes", {})
        stability = assignment.get("stability_score", 0)
        conditions_met = assignment.get("conditions_met", {})

        messages = [
            {
                "role": "system",
                "content": """당신은 반배정 결과를 설명하는 AI 전문가입니다.
배정 결과를 분석하고 교사에게 상세한 배치 근거를 제공해주세요.

출력 형식 (JSON만 출력):
{
  "summary": "전체 배정 요약 (3-4문장, 핵심 결정 사항 포함)",
  "conditions_summary": [
    "각 절대 조건 처리 결과 (예: 김민준-이서연 갈등 관계로 인해 다른 반 배치)",
    "특수교육 학생 배치 결과",
    "성비/성적 균등 배분 결과",
    "리더십 학생 분산 결과"
  ],
  "student_reasons": {
    "학생명": "이 학생이 이 반에 배정된 구체적 이유"
  },
  "parent_response": "학부모 민원 대응용 공식 답변 (3-4문장)"
}

규칙:
- conditions_summary는 최소 4개 이상 구체적으로 작성
- 갈등 관계, 친한 친구, 특수교육 등 각각 언급
- 반 번호를 포함해서 설명 (예: 1반에 배치됨)
- 특수교육 학생은 개별 이름 언급하지 말고 "특수교육 대상자들"로 통칭
- 갈등/같은반 조건 학생 이름은 언급 가능 (절대 조건이므로)
- summary 마지막에 반드시 "모든 반이 안정적인 학급 운영이 가능할 것으로 판단됩니다."로 마무리
- "1반과 2반, 3반 모두" 같은 특정 반 나열 대신 "모든 반"으로 표현
- 객관적이고 전문적인 어투
- JSON만 출력"""
            },
            {
                "role": "user",
                "content": f"""배정 결과:
- 1반: {', '.join(classes.get('class_1', []))}
- 2반: {', '.join(classes.get('class_2', []))}
- 3반: {', '.join(classes.get('class_3', []))}

학급 안정성 지수: {stability}점

학생 정보: {', '.join(student_info)}
갈등 관계: {', '.join(conflict_info) if conflict_info else '없음'}
친한 관계: {', '.join(friend_info) if friend_info else '없음'}

충족된 조건: {', '.join(conditions_met.get('met', []))}
미충족 조건: {', '.join(conditions_met.get('unmet', []))}

위 배정 결과에 대한 설명을 JSON으로 작성해주세요."""
            }
        ]

        try:
            result = await self._call(messages, max_tokens=1500)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:]
            return json.loads(result)
        except Exception as e:
            return {
                "summary": f"AI 배치 결과 - 학급 안정성 지수 {stability}점",
                "student_reasons": {},
                "conditions_summary": conditions_met.get("met", []),
                "parent_response": f"본 반배정은 Class-Twin AI 최적화 시스템을 통해 학급 안정성 지수 {stability}점으로 결정되었습니다."
            }

    # ==================== 문서 생성 ====================

    async def generate_document(self, assignment: Dict, students: List[Dict], doc_type: str) -> str:
        """배정 결과 문서 자동 생성"""
        classes = assignment.get("classes", {})
        stability = assignment.get("stability_score", 0)

        type_prompts = {
            "teacher": "교사용 반배정 결과 보고서를 작성해주세요. 배정 방법, 조건 충족 여부, 특이사항을 포함하세요.",
            "student_reasons": "학생별 반배정 이유서를 작성해주세요. 각 학생의 배정 근거를 간략히 설명하세요.",
            "parent_response": "학부모 민원 대응용 공식 문서를 작성해주세요. 객관적이고 공식적인 어투로 배정 근거를 설명하세요."
        }

        messages = [
            {"role": "system", "content": "당신은 학교 행정 문서 작성 AI입니다. 요청된 문서를 전문적으로 작성해주세요."},
            {"role": "user", "content": f"""
{type_prompts.get(doc_type, '문서를 작성해주세요.')}

배정 정보:
- 1반: {', '.join(classes.get('class_1', []))}
- 2반: {', '.join(classes.get('class_2', []))}
- 3반: {', '.join(classes.get('class_3', []))}
- 학급 안정성 지수: {stability}점
- 배정 방법: Class-Twin AI 최적화 알고리즘
"""}
        ]

        try:
            return await self._call(messages, max_tokens=1000)
        except:
            return f"[{doc_type} 문서] 학급 안정성 지수 {stability}점으로 배정 완료"

    async def generate_seat_document(self, seat_result: Dict, doc_type: str = "teacher") -> str:
        """자리배정 결과 문서 자동 생성"""
        equity = seat_result.get("equity_score", 0)
        conflicts = seat_result.get("conflict_adjacent_pairs", 0)
        alerts = seat_result.get("alerts", [])
        seat_grid = seat_result.get("seat_grid", [])

        # 그리드를 텍스트로 직렬화 (LLM이 위치 감각을 가질 수 있게)
        grid_lines = []
        for r, row in enumerate(seat_grid):
            cells = [(cell.get("name") or "·") for cell in row]
            grid_lines.append(f"  {r+1}열: " + " | ".join(cells))
        grid_text = "\n".join(grid_lines) if grid_lines else "(좌석 정보 없음)"
        alert_text = "\n".join(f"- [{a.get('type','info')}] {a.get('message','')}" for a in alerts) or "- 없음"

        type_prompts = {
            "teacher": "교사용 자리배정 결과 보고서를 작성해주세요. 배치 원칙, 형평성, 갈등 인접 여부, 특이사항을 포함하세요.",
            "student_reasons": "학생별 자리 배치 이유서를 작성해주세요. 시력 배려, 특수교육, 갈등 회피 등 근거를 설명하세요.",
            "parent_response": "학부모 민원 대응용 자리배정 근거 문서를 작성해주세요. 객관적이고 공식적인 어투로 작성하세요.",
        }

        messages = [
            {"role": "system", "content": "당신은 학교 행정 문서 작성 AI입니다. 요청된 자리배정 문서를 전문적으로 작성해주세요."},
            {"role": "user", "content": f"""
{type_prompts.get(doc_type, '자리배정 문서를 작성해주세요.')}

자리배정 정보:
- 형평성 지수: {equity}
- 갈등 인접 쌍: {conflicts}쌍
- 좌석 배치 (앞→뒤):
{grid_text}

알림:
{alert_text}

배정 방법: Class-Twin Seat 알고리즘 (이력 기반 형평성 보정)
"""}
        ]

        try:
            return await self._call(messages, max_tokens=1000)
        except Exception as e:
            return f"[{doc_type} 문서] 형평성 {equity}, 갈등 인접 {conflicts}쌍으로 배정 완료"

    async def explain_seat_for_student(self, seat_result: Dict, student_name: str) -> str:
        """특정 학생 한 명에 대한 자리 배치 이유 — 2~3문장, 빠른 응답"""
        # 해당 학생의 셀 찾기
        cell = None
        rows = cols = 0
        for r, row in enumerate(seat_result.get("seat_grid", [])):
            cols = max(cols, len(row))
            for c, cellv in enumerate(row):
                if cellv and cellv.get("name") == student_name:
                    cell = cellv
                    cell["_r"] = r
                    cell["_c"] = c
            rows = max(rows, r + 1)
        if not cell:
            return f"{student_name} 학생을 자리 배치 결과에서 찾을 수 없습니다."

        flags = cell.get("flags") or []
        seat_num = cell.get("seat_num")
        r, c = cell["_r"], cell["_c"]
        # 자리 위치 텍스트
        position = []
        if r == 0: position.append("앞줄")
        elif r == rows - 1: position.append("뒷줄")
        else: position.append(f"{r+1}번째 줄")
        if c == 0 or c == cols - 1: position.append("창가")
        position_text = " ".join(position)

        flag_text = ", ".join(flags) if flags else "특이사항 없음"
        prompt = (
            f"학생 이름: {student_name}\n"
            f"좌석 번호: {seat_num} ({position_text})\n"
            f"분류: {flag_text}\n"
            f"성적: {cell.get('grade', '미상')}\n"
            f"성별: {cell.get('gender', '미상')}\n\n"
            "이 학생이 위 자리에 배치된 이유를 2~3문장으로 자연스럽게 설명해주세요. "
            "분류(시력배려/특수교육/갈등주의 등)가 있으면 그 이유와 연결해서 설명하고, "
            "없으면 일반 배치임을 짧게 언급하세요."
        )
        messages = [
            {"role": "system", "content": "당신은 학급 운영 전문가입니다. 자리 배치의 근거를 교사에게 짧고 명확하게 설명합니다."},
            {"role": "user", "content": prompt},
        ]
        try:
            return await self._call(messages, max_tokens=200)
        except Exception as e:
            return f"{student_name}: {position_text} 배치 ({flag_text})"

    # ==================== 소견 분석 ====================

    async def analyze_teacher_notes(self, students: List[Dict]) -> List[Dict]:
        """
        교사 소견 텍스트를 분석해서 수치로 변환
        "리더십 강하나 방향이 부정적" → leadership: 8, negative_influence: 7
        """
        # 130명 한번에 보내면 토큰 초과 → 30명씩 배치 처리
        batch_size = 20  # 토큰 초과 방지를 위해 20명씩
        all_results = []

        for i in range(0, len(students), batch_size):
            batch = students[i:i+batch_size]
            notes_text = "\n".join([
                f"{s['name']}: {s.get('teacher_note', '소견 없음')}"
                for s in batch if s.get("teacher_note")
            ])
            if not notes_text.strip():
                continue

            messages = [
                {
                    "role": "system",
                    "content": """교사 소견을 읽고 학생별 특성을 수치화하세요.
어떤 형식의 소견이든 핵심 특성을 파악해서 아래 형식으로 변환해주세요.

출력 형식 (JSON만 출력):
[
  {
    "name": "학생명",
    "leadership": 1-10,
    "sociability": 1-10,
    "attention": 1-10,
    "positive_influence": 1-10,
    "tags": ["리더형", "학업우수", "전학생", "고립위험", "창의적", "사회적" 등]
  }
]

수치 기준:
- leadership: 리더십, 모범생, 반장 기질 → 높음
- sociability: 교우관계 원만, 친화력, 배려심 → 높음
- attention: 집중력, 학업 성취도, 성실함 → 높음
- positive_influence: 긍정적 영향, 분위기 메이커 → 높음

소견에 명시 안 된 항목은 5(중간)으로 설정.
반드시 JSON만 출력."""
                },
                {
                    "role": "user",
                    "content": f"다음 교사 소견을 분석해주세요:\n{notes_text}"
                }
            ]

            try:
                result = await self._call(messages, max_tokens=6000)
                result = result.strip()
                if result.startswith("```"):
                    result = result.split("```")[1]
                    if result.startswith("json"):
                        result = result[4:].strip()
                    result = result.rstrip("```").strip()
                batch_result = json.loads(result)
                all_results.extend(batch_result)
                print(f"✅ 배치 {i//batch_size+1} 파싱 성공: {len(batch_result)}명")
            except Exception as e:
                print(f"❌ 배치 {i//batch_size+1} 파싱 실패: {e}")
                continue

        if not all_results:
            return []

        # 이후 messages/try 블록은 이미 위에서 처리됨
        notes_text = ""  # 더미 (아래 코드 실행 방지)
        if not notes_text.strip():
            return all_results

        messages = [
            {
                "role": "system",
                "content": """교사 소견을 읽고 학생별 특성을 수치화하세요.
어떤 형식의 소견이든 핵심 특성을 파악해서 아래 형식으로 변환해주세요.

출력 형식 (JSON만 출력):
[
  {
    "name": "학생명",
    "leadership": 1-10,
    "sociability": 1-10,
    "attention": 1-10,
    "positive_influence": 1-10,
    "tags": ["리더형", "학업우수", "전학생", "고립위험", "창의적", "사회적" 등]
  }
]

수치 기준:
- leadership: 리더십, 모범생, 반장 기질 → 높음
- sociability: 교우관계 원만, 친화력, 배려심 → 높음
- attention: 집중력, 학업 성취도, 성실함 → 높음
- positive_influence: 긍정적 영향, 분위기 메이커 → 높음

소견에 명시 안 된 항목은 5(중간)으로 설정.
반드시 JSON만 출력."""
            },
            {
                "role": "user",
                "content": f"다음 교사 소견을 분석해주세요:\n{notes_text}"
            }
        ]

        try:
            result = await self._call(messages, max_tokens=6000)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:].strip()
                result = result.rstrip("```").strip()
            parsed = json.loads(result)
            print(f"✅ 소견 파싱 성공: {len(parsed)}명")
            return parsed
        except Exception as e:
            print(f"❌ 소견 파싱 실패: {e}")
            print(f"   응답 앞부분: {result[:200] if 'result' in dir() else 'N/A'}")
            return []