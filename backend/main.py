from dotenv import load_dotenv
load_dotenv()  # 반드시 다른 import보다 먼저 실행

import asyncio
import os
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import uvicorn

from database import db
from algorithm import ClassAssigner, SeatAssigner
from ai import AI

app = FastAPI(title="Class Twin AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ai = AI()

# ==================== 모델 ====================

class Student(BaseModel):
    name: str
    gender: str           # 남/여
    academic_level: str   # 상/중/하
    height: int
    vision: str           # 정상/약함
    attention_level: str  # 높음/중간/낮음
    special_needs: Optional[str] = None  # ADHD/학습장애/None
    teacher_note: Optional[str] = None

class Relation(BaseModel):
    student_a: str        # 학생 이름
    student_b: str
    type: str             # 갈등/친함
    severity: Optional[str] = "중간"  # 높음/중간/낮음

class Condition(BaseModel):
    absolute: List[dict]  # 절대 조건 [{type, student_a, student_b}]
    balance: List[dict]   # 균형 조건 [{label, priority}]
    chat_input: str       # 챗봇 자유 입력
    num_classes: int = 3  # 반 개수
    variant: int = 0      # 배치안 변형 (0=A, 1=B, 2=C) - 다른 결과 생성용

class SeatCondition(BaseModel):
    class_id: int
    absolute: List[dict]
    balance: List[dict]
    extra_note: Optional[str] = None
    rows: Optional[int] = 5
    cols: Optional[int] = 6

# ==================== 1단계: 학생 데이터 ====================

@app.get("/students")
async def get_students():
    """전체 학생 목록 조회"""
    students = db.get_all_students()
    return {"students": students, "total": len(students)}

@app.post("/students")
async def add_student(student: Student):
    """학생 추가"""
    result = db.add_student(student.dict())
    return {"success": True, "student": result}

@app.post("/students/bulk")
async def add_students_bulk(students: List[Student]):
    """학생 여러 명 한 번에 추가"""
    results = []
    for s in students:
        result = db.add_student(s.dict())
        results.append(result)
    return {"success": True, "count": len(results)}

@app.delete("/students/{student_id}")
async def delete_student(student_id: int):
    db.delete_student(student_id)
    return {"success": True}

@app.delete("/students")
async def clear_all_students():
    """전체 학생 데이터 초기화 (업로드 전 리셋용)"""
    db.clear_students()
    return {"success": True}

@app.delete("/relations")
async def clear_all_relations():
    """전체 관계 데이터 초기화"""
    db.clear_relations()
    return {"success": True}

@app.post("/relations")
async def add_relation(relation: Relation):
    """관계 데이터 추가 (갈등/친함)"""
    result = db.add_relation(relation.dict())
    return {"success": True, "relation": result}

@app.get("/relations")
async def get_relations():
    relations = db.get_all_relations()
    return {"relations": relations}

# ==================== 2단계: 챗봇 조건 파싱 ====================

@app.post("/conditions/parse")
async def parse_conditions(data: dict):
    """
    교사가 자연어로 입력한 조건을 GPT-4o가 파싱
    예: "민준이랑 서연이 분리해주세요"
    → {type: "분리", student_a: "김민준", student_b: "이서연"}
    """
    chat_input = data.get("chat_input", "")
    students = db.get_all_students()
    student_names = [s["name"] for s in students]

    parsed = await ai.parse_conditions(chat_input, student_names)
    return {"parsed_conditions": parsed, "original": chat_input}

@app.post("/conditions/chat")
async def chat_with_ai(data: dict):
    """챗봇 대화 - 조건 입력 도우미 (ai_traits 포함)"""
    message = data.get("message", "")
    history = data.get("history", [])
    students = db.get_all_students()  # ai_traits 포함된 데이터
    
    response = await ai.chat_response(message, history, students)
    parsed_conditions = response.get("parsed_conditions", [])
    question_type = response.get("question_type", "clear_condition")
    
    # 🔥 하이브리드 필터링: 질문 패턴 강제 필터링
    question_keywords = ["누가", "누구", "이름", "알려", "말해", "있지", "있어", "누구지", "어떤", "어느"]
    if any(keyword in message for keyword in question_keywords):
        # 질문으로 판단 → 조건 추가 안 함
        question_type = "simple_question"
        parsed_conditions = []
    
    # trait_balance 조건에 학생 이름 추가
    for cond in parsed_conditions:
        if cond.get("type") == "trait_balance":
            trait = cond.get("trait")
            threshold = cond.get("threshold", 7)
            trait_students = [
                s["name"] for s in students
                if s.get("ai_traits", {}).get(trait, 0) >= threshold
            ]
            cond["student_names"] = trait_students
    
    condition = parsed_conditions[0] if parsed_conditions else response.get("condition")
    
    return {
        "response": response["message"],
        "extracted_condition": condition,
        "parsed_conditions": parsed_conditions,
        "question_type": question_type
    }

# 백그라운드 소견 파싱 상태
_notes_parsing_status = {"done": False, "count": 0}

# ==================== 3단계: 반배정 알고리즘 ====================

@app.post("/assignments/class/generate")
async def generate_class_assignment(conditions: Condition):
    """
    반배정 전체 파이프라인 (개선 버전)
    1. 데이터 로드 및 챗봇 조건 파싱
    2. LLM을 통한 교사 소견 분석 및 학생 특성(ai_traits) 주입
    3. 알고리즘으로 최적 배치 계산 (AI 특성 반영)
    4. LLM으로 배치 결과에 대한 자연어 설명 생성
    """
    students = db.get_all_students()
    relations = db.get_all_relations()

    if not students:
        raise HTTPException(status_code=400, detail="학생 데이터가 없습니다")

    # Step 1: 챗봇 입력 파싱 (자연어 조건 처리)
    extra_conditions = []
    if conditions.chat_input and conditions.chat_input.strip():
        try:
            student_names = [s["name"] for s in students]
            parsed = await ai.parse_conditions(conditions.chat_input, student_names)
            extra_conditions = parsed
            print(f"✅ 챗봇 조건 파싱: {conditions.chat_input[:50]} → {len(extra_conditions)}개")
            for ec in extra_conditions:
                print(f"   파싱된 조건: {ec}")
        except Exception as e:
            print(f"❌ 조건 파싱 에러: {e}")

    # Step 2: 교사 소견 LLM 분석 → 학생 데이터에 ai_traits 반영
    # 이 과정에서 leadership, sociability 등의 수치가 학생 데이터에 추가됩니다.
    # DB에서 ai_traits가 이미 포함된 학생 데이터를 가져옴
    traits_count = sum(1 for s in students if s.get("ai_traits"))
    print(f"📝 ai_traits 보유 학생: {traits_count}명 / 전체 {len(students)}명") 

    # Step 3: 알고리즘 배정 실행
    # 관계 데이터에서 심각도 높음인 것을 자동으로 절대 조건에 추가
    auto_from_relations = []
    for r in relations:
        # 갈등: 중간 이상 → 분리 / 친함: 높음만 → 같은 반
        if r["type"] == "갈등" and r.get("severity") in ["높음", "중간"]:
            ctype = "분리"
        elif r["type"] == "친함" and r.get("severity") == "높음":
            ctype = "같은 반"
        else:
            continue
        already = any(
            c.get("student_a") == r["student_a"] and
            c.get("student_b") == r["student_b"] and
            c.get("type") == ctype
            for c in conditions.absolute
        )
        if not already:
            auto_from_relations.append({
                "type": ctype,
                "student_a": r["student_a"],
                "student_b": r["student_b"],
                "students": [r["student_a"], r["student_b"]]
            })

    # 절대조건 합치기 + 중복 제거 + 유효하지 않은 조건 제거
    raw_conditions = conditions.absolute + extra_conditions + auto_from_relations
    seen_conds = set()
    clean_conditions = []
    for c in raw_conditions:
        ctype = c.get("type", "")
        sa = c.get("student_a", "") or ""
        sb = c.get("student_b", "") or ""
        # 분리/같은반 조건만 중복 체크 (특수교육 같은 단일 조건은 그대로)
        if ctype in ["분리", "같은 반"]:
            if not sa or not sb or sb == "None":
                continue  # student_b 없는 잘못된 조건 제외
            key = (ctype, min(sa, sb), max(sa, sb))
            if key in seen_conds:
                continue
            seen_conds.add(key)
        clean_conditions.append(c)

    all_conditions = {
        "absolute": clean_conditions,
        "balance": conditions.balance
    }
    sep = [c for c in clean_conditions if c.get('type') == '분리']
    same = [c for c in clean_conditions if c.get('type') == '같은 반']
    trait_bal = [c for c in clean_conditions if c.get('type') == 'trait_balance']
    print(f"✅ 절대조건: 분리 {len(sep)}개, 같은반 {len(same)}개, trait_balance {len(trait_bal)}개")
    for c in sep + same:
        print(f"   → {c.get('type')}: {c.get('student_a')} ↔ {c.get('student_b')}")
    
    # 전달받은 num_classes를 사용 (기본값 3)
    assigner = ClassAssigner(students, relations, all_conditions, variant=conditions.variant)
    result = assigner.assign(num_classes=conditions.num_classes)

    # 결과 DB 저장
    assignment_id = db.save_class_assignment(result)
    result["assignment_id"] = assignment_id

    result["parsed_chat_conditions"] = extra_conditions

    # 리더십 높은 학생 목록 추가 (ai_traits.leadership >= 7)
    leader_students = [
        {"name": s["name"], "leadership": s.get("ai_traits", {}).get("leadership", 0)}
        for s in students
        if s.get("ai_traits", {}).get("leadership", 0) >= 7
    ]
    leader_students.sort(key=lambda x: -x["leadership"])
    result["leader_students"] = leader_students

    return result

@app.get("/assignments/class/{assignment_id}")
async def get_class_assignment(assignment_id: int):
    result = db.get_class_assignment(assignment_id)
    return result

# ==================== 4단계: 안정성 지수 ====================

@app.post("/assignments/class/{assignment_id}/analyze")
async def analyze_stability(assignment_id: int):
    """
    배치 결과의 안정성 지수 상세 분석
    - 갈등 쌍 수
    - 고립 학생 수
    - 성적 균등도
    - 성비 균등도
    - 리더십 분산도
    """
    assignment = db.get_class_assignment(assignment_id)
    students = db.get_all_students()
    relations = db.get_all_relations()

    assigner = ClassAssigner(students, relations, {})
    analysis = assigner.analyze_stability(assignment["classes"])

    return analysis

# ==================== 5단계: 배치 이유 설명 ====================

@app.post("/assignments/class/{assignment_id}/explain")
async def explain_assignment(assignment_id: int):
    """
    GPT-4o가 배치 결과의 이유를 자연어로 설명
    - 학생별 배정 근거
    - 충족된 조건 목록
    - 학부모 민원 대응 문서
    """
    assignment = db.get_class_assignment(assignment_id)
    students = db.get_all_students()
    relations = db.get_all_relations()

    explanation = await ai.explain_assignment(assignment, students, relations)
    return explanation

# ==================== 자리배정 ====================

def _simple_parse_extra_note(text: str, student_names: list) -> list:
    """LLM 없이 동작하는 규칙 기반 파서. 학생 이름 + 핵심 키워드만 매칭.
    LLM 호출이 실패했을 때 fallback으로 사용."""
    if not text or not text.strip():
        return []
    results = []
    # 학생 이름을 길이순(긴 것 먼저)으로 정렬해 부분 매칭 오류 방지
    names_sorted = sorted(student_names, key=len, reverse=True)
    # 줄 단위로 처리
    for raw_line in text.replace(",", "\n").replace("·", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        # 이 줄에 등장하는 학생 이름들 추출
        found = []
        for name in names_sorted:
            if name in line and name not in found:
                found.append(name)
        if not found:
            continue
        # 키워드 판정
        is_separate = any(k in line for k in ["분리", "떨어뜨", "따로", "멀리", "거리"])
        is_together = any(k in line for k in ["옆자리", "옆에", "같이", "함께", "붙여", "나란히", "가까이"])
        is_teacher = any(k in line for k in ["교사 근처", "교사근처", "교탁 근처", "교탁근처", "선생님 근처", "교탁 옆"])
        is_front = any(k in line for k in ["앞자리", "앞 자리", "앞으로", "앞쪽", "맨 앞", "앞에"])
        if is_separate and len(found) >= 2:
            results.append({"type": "분리", "student_a": found[0], "student_b": found[1], "note": line})
        elif is_together and len(found) >= 2:
            results.append({"type": "같이 앉기", "student_a": found[0], "student_b": found[1], "note": line})
        elif is_teacher:
            for n in found:
                results.append({"type": "교사 근처", "student_a": n, "note": line})
        elif is_front:
            for n in found:
                results.append({"type": "앞자리", "student_a": n, "note": line})
    return results


@app.post("/assignments/seat/generate")
async def generate_seat_assignment(conditions: SeatCondition):
    """
    자리배정 전체 파이프라인
    1. 데이터 로드 (학생, 관계, 이전 자리 이력)
    2. 알고리즘 기반 최적 자리 배치
    3. LLM(AI)을 통한 배치 결과 분석 및 이유 생성
    """
    # 1. 데이터 가져오기
    students = db.get_students_by_class(conditions.class_id)
    if not students:
        # 배정된 학생이 없으면 전체 학생을 대상으로 함 (테스트용)
        students = db.get_all_students()
        
    relations = db.get_all_relations()
    seat_history = db.get_seat_history(conditions.class_id)

    if not students:
        raise HTTPException(status_code=400, detail="배정할 학생 데이터가 없습니다.")

    # [신규] extra_note LLM 파싱 → 절대조건에 자동 추가
    extra_conditions = []
    print(f"📝 자리배정 요청 도착: extra_note={conditions.extra_note!r}")
    if conditions.extra_note and conditions.extra_note.strip():
        student_names = [s["name"] for s in students]
        try:
            extra_conditions = await ai.parse_conditions(conditions.extra_note, student_names)
            print(f"✅ 자리배정 추가사항 LLM 파싱: {len(extra_conditions)}개")
        except Exception as e:
            print(f"❌ 자리배정 추가사항 LLM 파싱 에러: {e}")
        # LLM이 빈 결과거나 실패했으면 규칙 기반 fallback 사용
        if not extra_conditions:
            extra_conditions = _simple_parse_extra_note(conditions.extra_note, student_names)
            if extra_conditions:
                print(f"🔧 자리배정 추가사항 fallback 파싱: {len(extra_conditions)}개")
            else:
                print(f"⚠️ 추가사항 파싱 결과 0개 (LLM도 fallback도 매칭 실패)")
        for ec in extra_conditions:
            print(f"   {ec}")
    else:
        print("📝 extra_note 없음")

    # 절대조건 + 자동 추가 조건 합치기
    merged_conditions = conditions.model_dump()
    merged_conditions["absolute"] = list(conditions.absolute) + extra_conditions

    # 2. 알고리즘 실행 (rows/cols 동적 전달)
    assigner = SeatAssigner(students, relations, merged_conditions, seat_history)
    print(f"⚖️ balance 수신: {conditions.balance}")
    print(f"⚖️ 계산된 가중치: {assigner.balance_weights}")
    result = assigner.assign(rows=conditions.rows or 5, cols=conditions.cols or 6)

    # 2-1. 추가고려사항 해석 결과를 사람 친화적 메시지로 변환 (UI 표시용)
    extra_interpretations = []
    if extra_conditions:
        # 학생 이름 → 실제 배치 위치 찾기 (result["seat_grid"]에서)
        name_to_pos = {}
        for row in result.get("seat_grid", []):
            for cell in row:
                if cell.get("name"):
                    name_to_pos[cell["name"]] = (cell["row"], cell["col"])

        rows_count = conditions.rows or 5

        for ec in extra_conditions:
            ctype = ec.get("type", "")
            a = ec.get("student_a") or ""
            b = ec.get("student_b") or ""

            if ctype in ("앞자리", "앞자리 배치"):
                pos = name_to_pos.get(a)
                if not pos:
                    continue
                r, _ = pos
                if r == 0:
                    msg = f"{a} → 1행 배치 완료 ✓"
                elif r <= 1:
                    msg = f"{a} → 앞 2줄 우선 배치 ({r+1}행) — 1행은 시력배려 학생으로 가득 차 1행 배치 불가"
                else:
                    msg = f"{a} → 앞자리 배치 시도했으나 {r+1}행 배치됨 (다른 조건과 충돌)"
                extra_interpretations.append(msg)

            elif ctype in ("교사 근처", "교사근처"):
                pos = name_to_pos.get(a)
                if not pos:
                    continue
                r, c = pos
                cols_count = conditions.cols or 6
                center_cols = {cols_count // 2 - 1, cols_count // 2}
                if r == 0 and c in center_cols:
                    msg = f"{a} → 교사 근처(앞 중앙) 배치 완료 ✓"
                else:
                    msg = f"{a} → 교사 근처 배치 시도, 현재 {r+1}행 {c+1}열"
                extra_interpretations.append(msg)

            elif ctype in ("분리", "갈등"):
                if not (a and b):
                    continue
                pa, pb = name_to_pos.get(a), name_to_pos.get(b)
                if pa and pb:
                    dist = abs(pa[0] - pb[0]) + abs(pa[1] - pb[1])
                    if dist >= 2:
                        msg = f"{a} ↔ {b} → 분리 배치 완료 ✓ (거리 {dist})"
                    else:
                        msg = f"{a} ↔ {b} → 분리 요청했으나 인접함 (다른 조건과 충돌)"
                    extra_interpretations.append(msg)

            elif ctype in ("같이", "같이 앉기", "옆자리", "같은 반"):
                if not (a and b):
                    continue
                pa, pb = name_to_pos.get(a), name_to_pos.get(b)
                if pa and pb:
                    same_row = pa[0] == pb[0]
                    col_diff = abs(pa[1] - pb[1])
                    if same_row and col_diff == 1:
                        msg = f"{a} ↔ {b} → 옆자리 배치 완료 ✓"
                    elif same_row and col_diff == 2:
                        msg = f"{a} ↔ {b} → 같은 줄 근처 배치 (한 칸 건너) — 완벽한 옆자리는 다른 조건과 충돌"
                    else:
                        msg = f"{a} ↔ {b} → 옆자리 요청했으나 떨어짐 (다른 조건과 충돌)"
                    extra_interpretations.append(msg)

            elif ctype == "기타":
                note = ec.get("note") or ""
                if note:
                    extra_interpretations.append(f"기타 참고: {note}")

    result["extra_interpretations"] = extra_interpretations

    # 3. LLM(AI) 배치 이유 설명 추가 (선택 사항)
    try:
        # ai.py에 자리배정 설명 로직이 있다면 호출 (현재는 반배정 설명 로직 활용 가능)
        # 간단한 분석 메시지를 AI에게 요청하도록 구성
        prompt = f"자리배정 결과: {result['alerts']}. 형평성 점수: {result['equity_score']}. 이 결과를 바탕으로 교사에게 줄 짧은 조언을 작성해줘."
        
        # ai._call을 직접 활용하거나 간단한 메시지 생성
        ai_message = await ai._call([
            {"role": "system", "content": "당신은 학급 운영 전문가입니다. 자리 배치 결과를 보고 교사에게 따뜻하고 전문적인 조언을 한 문장으로 해주세요."},
            {"role": "user", "content": prompt}
        ])
        result["ai_advice"] = ai_message
    except Exception as e:
        print(f"AI 자리분석 에러: {e}")
        result["ai_advice"] = "배정이 완료되었습니다. 갈등 인접 여부를 확인해주세요."

    # 4. 결과 저장 및 반환
    seat_id = db.save_seat_assignment(result, class_id=conditions.class_id)
    result["seat_id"] = seat_id
    
    return result

@app.get("/assignments/seat/history/{class_id}")
async def get_seat_history(class_id: int):
    """자리 배정 이력 - 형평성 계산용"""
    history = db.get_seat_history(class_id)
    equity = db.calculate_equity(class_id)
    return {"history": history, "equity": equity}

class SeatEvaluateRequest(BaseModel):
    class_id: int
    seat_grid: List[List[dict]]

@app.post("/assignments/seat/evaluate")
async def evaluate_seat_grid(req: SeatEvaluateRequest):
    """수동 조정 후 갈등 인접 쌍 / 형평성 / 알림 / 셀 flags 재계산"""
    students = db.get_students_by_class(req.class_id) or db.get_all_students()
    relations = db.get_all_relations()
    seat_history = db.get_seat_history(req.class_id)
    assigner = SeatAssigner(students, relations, {"absolute": [], "balance": []}, seat_history)
    student_map = {s["name"]: s for s in students}

    rows = len(req.seat_grid)
    cols = len(req.seat_grid[0]) if rows else 0
    seats = {}
    for r, row in enumerate(req.seat_grid):
        for c, cell in enumerate(row):
            if cell.get("name"):
                seats[(r, c)] = cell["name"]

    # 새 grid에 flags를 다시 박아서 돌려줌 (수동 swap 후 갈등주의 등 재분류용)
    updated_grid = []
    for r in range(rows):
        row_data = []
        for c in range(cols):
            src_cell = req.seat_grid[r][c] if c < len(req.seat_grid[r]) else {}
            name = src_cell.get("name")
            if not name:
                row_data.append({"row": r, "col": c, "name": None, "seat_num": r * cols + c + 1})
                continue
            student = student_map.get(name, {})
            flags = []
            if student.get("vision") == "약함":
                flags.append("시력배려")
            if student.get("special_needs") and student.get("special_needs") not in ("없음", "일반", None):
                flags.append("특수교육")
            in_conflict = any(name in pair for pair in assigner.conflict_pairs)
            if in_conflict:
                flags.append("갈등주의")
            row_data.append({
                "row": r, "col": c,
                "name": name,
                "seat_num": r * cols + c + 1,
                "gender": student.get("gender"),
                "grade": student.get("academic_level"),
                "special": assigner._get_special_type(student),
                "special_needs": student.get("special_needs"),
                "flags": flags,
                "tag": assigner._get_tag(student),
            })
        updated_grid.append(row_data)

    return {
        "conflict_adjacent_pairs": assigner._count_adjacent_conflicts(seats, rows, cols),
        "equity_score": assigner._calc_equity_score(seats, rows, cols),
        "alerts": assigner._generate_alerts(seats, rows, cols),
        "seat_grid": updated_grid,
        "rows": rows,
        "cols": cols,
    }

class SeatStudentReasonRequest(BaseModel):
    seat_result: dict
    student_name: str

@app.post("/assignments/seat/student-reason")
async def seat_student_reason(req: SeatStudentReasonRequest):
    """특정 학생 한 명의 자리 배정 이유 — 짧고 빠름"""
    text = await ai.explain_seat_for_student(req.seat_result, req.student_name)
    return {"student_name": req.student_name, "reason": text}

class SeatDocumentRequest(BaseModel):
    seat_result: dict
    doc_type: Optional[str] = "teacher"
    seat_id: Optional[int] = None

@app.post("/documents/seat")
async def generate_seat_document(req: SeatDocumentRequest):
    """자리배정 결과 문서 생성 (teacher / student_reasons / parent_response)"""
    doc = await ai.generate_seat_document(req.seat_result, req.doc_type or "teacher")
    doc_id = db.save_seat_document(req.seat_id, req.doc_type or "teacher", doc)
    return {"document": doc, "type": req.doc_type or "teacher", "doc_id": doc_id}

# ==================== 문서 생성 ====================

@app.post("/documents/class/{assignment_id}")
async def generate_class_document(assignment_id: int, doc_type: str = "teacher"):
    """
    배정 결과 문서 자동 생성
    doc_type: teacher / student_reasons / parent_response
    """
    assignment = db.get_class_assignment(assignment_id)
    students = db.get_all_students()

    doc = await ai.generate_document(assignment, students, doc_type)
    doc_id = db.save_class_document(assignment_id, doc_type, doc)
    return {"document": doc, "type": doc_type, "doc_id": doc_id}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

# ==================== Excel/CSV 업로드 ====================

from fastapi import UploadFile, File
import io

@app.post("/students/upload")
async def upload_students(file: UploadFile = File(...)):
    """
    Excel(.xlsx) 또는 CSV 파일로 학생 데이터 일괄 업로드
    컬럼: 이름 | 성별 | 성적 | 키(cm) | 시력 | 주의력 | 특수교육 | 교사소견
    """
    try:
        import pandas as pd
        contents = await file.read()

        # 파일 형식 자동 감지
        relations_df = None  # 두 번째 시트(관계데이터)가 있으면 채워짐
        if file.filename.endswith(".csv"):
            df = pd.read_csv(
                io.BytesIO(contents),
                encoding="utf-8-sig"  # BOM 처리
            )
        elif file.filename.endswith((".xlsx", ".xls")):
            xls = pd.ExcelFile(io.BytesIO(contents))
            # 학생 시트: 첫 번째 시트 또는 '학생목록' 우선
            student_sheet = "학생목록" if "학생목록" in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=student_sheet, header=0)
            # 안내 행 제거
            if len(df) > 0:
                first_val = str(df.iloc[0, 0]).strip()
                if first_val.startswith("예)") or first_val.startswith("예:") or first_val == "예시":
                    df = df.iloc[1:].reset_index(drop=True)
            # 관계 시트 자동 감지
            for cand in ("관계데이터", "관계", "relations"):
                if cand in xls.sheet_names:
                    relations_df = pd.read_excel(xls, sheet_name=cand, header=0)
                    if len(relations_df) > 0:
                        first_val = str(relations_df.iloc[0, 0]).strip()
                        if first_val.startswith("예)") or first_val.startswith("예:"):
                            relations_df = relations_df.iloc[1:].reset_index(drop=True)
                    break
        else:
            raise HTTPException(status_code=400, detail=".xlsx 또는 .csv 파일만 지원합니다")

        # 컬럼명 앞뒤 공백 제거 후 정규화
        df.columns = [str(c).strip() for c in df.columns]
        col_map = {
            "이름": "name", "name": "name",
            "성별": "gender", "gender": "gender",
            "성적": "academic_level", "academic_level": "academic_level",
            "키(cm)": "height", "키": "height", "height": "height",
            "시력": "vision", "vision": "vision",
            "주의력": "attention_level", "attention_level": "attention_level",
            "특수교육": "special_needs", "special_needs": "special_needs",
            "교사소견": "teacher_note", "교사 소견": "teacher_note",
            "소견": "teacher_note", "teacher_note": "teacher_note",
        }
        df = df.rename(columns=col_map)

        # 필수 컬럼 체크
        if "name" not in df.columns:
            raise HTTPException(status_code=400, detail="'이름' 컬럼이 없습니다")

        # 빈 행 제거
        df = df.dropna(subset=["name"])
        df = df[df["name"].astype(str).str.strip() != ""]

        # 중복 이름 제거 (마지막 행 우선)
        before = len(df)
        dupes = df[df.duplicated(subset=["name"], keep=False)]["name"].tolist()
        if dupes:
            print(f"⚠ 중복 이름 발견: {list(set(dupes))}")
        df = df.drop_duplicates(subset=["name"], keep="last")
        after = len(df)
        if before != after:
            print(f"⚠ 중복 학생 {before-after}명 제거됨 → {after}명 유지")

        # 데이터 정제
        def clean_special(val):
            if pd.isna(val) or str(val).strip() in ["없음", "None", ""]:
                return None
            return str(val).strip()

        students = []
        for _, row in df.iterrows():
            student = {
                "name": str(row.get("name", "")).strip(),
                "gender": str(row.get("gender", "남")).strip(),
                "academic_level": str(row.get("academic_level", "중")).strip(),
                "height": int(row.get("height", 160)) if pd.notna(row.get("height", 160)) else 160,
                "vision": str(row.get("vision", "정상")).strip(),
                "attention_level": str(row.get("attention_level", "중간")).strip(),
                "special_needs": clean_special(row.get("special_needs")),
                "teacher_note": str(row.get("teacher_note", "")).strip() or None,
            }
            students.append(student)

        # DB 저장
        results = []
        for s in students:
            result = db.add_student(s)
            results.append(result)

        # 관계 시트가 있으면 같이 저장
        relations_added = 0
        if relations_df is not None and len(relations_df) > 0:
            rel_col_map = {
                "학생A": "student_a", "학생a": "student_a", "student_a": "student_a",
                "학생B": "student_b", "학생b": "student_b", "student_b": "student_b",
                "관계유형": "type", "관계": "type", "type": "type",
                "심각도": "severity", "severity": "severity",
                "메모": "memo", "비고": "memo", "memo": "memo",
            }
            relations_df.columns = [str(c).strip() for c in relations_df.columns]
            relations_df = relations_df.rename(columns=rel_col_map)
            for _, row in relations_df.iterrows():
                a = str(row.get("student_a", "")).strip()
                b = str(row.get("student_b", "")).strip()
                t = str(row.get("type", "")).strip()
                if not a or not b or a == "nan" or b == "nan" or not t or t == "nan":
                    continue
                rel = {
                    "student_a": a,
                    "student_b": b,
                    "type": t,
                    "severity": str(row.get("severity", "중간")).strip() or "중간",
                    "memo": str(row.get("memo", "")).strip() if row.get("memo") is not None else "",
                }
                db.add_relation(rel)
                relations_added += 1
            print(f"🔗 관계 데이터 {relations_added}개 추가")

        # asyncio.create_task로 같은 프로세스 내 백그라운드 실행 (메모리 공유)
        asyncio.create_task(parse_teacher_notes_bg(results))
        print(f"🔄 백그라운드 소견 파싱 예약: {len(results)}명")

        return {
            "success": True,
            "count": len(results),
            "students": results,
            "relations_added": relations_added,
            "message": f"{len(results)}명의 학생 데이터가 업로드되었습니다 (관계 {relations_added}건)"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 처리 오류: {str(e)}")


async def parse_teacher_notes_bg(students: list):
    """업로드 후 백그라운드에서 교사 소견 파싱 → DB ai_traits 업데이트"""
    notes_students = [s for s in students if s.get("teacher_note")]
    if not notes_students:
        print("📝 교사 소견이 있는 학생 없음 - 파싱 스킵")
        return
    print(f"🔄 백그라운드 소견 파싱 시작: {len(notes_students)}명")
    _notes_parsing_status["done"] = False
    _notes_parsing_status["count"] = 0
    try:
        analyzed = await ai.analyze_teacher_notes(notes_students)
        # DB에 직접 저장
        updated = 0
        for a in analyzed:
            if db.update_student_traits(a["name"], a):
                updated += 1
        print(f"✅ 백그라운드 소견 파싱 완료: {updated}명 DB 업데이트 완료!")
        print(f"   → 파싱된 학생: {', '.join([a['name'] for a in analyzed[:5]])}{'...' if len(analyzed) > 5 else ''}")
        _notes_parsing_status["done"] = True
        _notes_parsing_status["count"] = updated
    except Exception as e:
        print(f"❌ 백그라운드 소견 파싱 실패: {e}")
        import traceback
        traceback.print_exc()


@app.post("/relations/upload")
async def upload_relations(file: UploadFile = File(...)):
    """
    Excel 관계데이터 시트로 관계 일괄 업로드
    컬럼: 학생A | 학생B | 관계유형 | 심각도 | 메모
    """
    try:
        import pandas as pd
        contents = await file.read()

        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents), encoding="utf-8-sig")
        else:
            # 시트명 유연하게 처리 (관계데이터, 관계 데이터, relations 등)
            xl = pd.ExcelFile(io.BytesIO(contents))
            sheet_names = xl.sheet_names
            print(f"엑셀 시트 목록: {sheet_names}")
            
            rel_sheet = None
            for name in sheet_names:
                if "관계" in name or "relation" in name.lower():
                    rel_sheet = name
                    break
            
            if rel_sheet is None:
                raise HTTPException(status_code=400, detail=f"관계 데이터 시트를 찾을 수 없습니다. 시트 목록: {sheet_names}")
            
            df = pd.read_excel(io.BytesIO(contents), sheet_name=rel_sheet, header=0)
            print(f"관계 시트 '{rel_sheet}' 로드: {len(df)}행, 컬럼: {list(df.columns)}")
            # 예시 행 제거
            if len(df) > 0 and df.iloc[0].astype(str).str.contains(r"예\)", regex=True).any():
                df = df.iloc[1:].reset_index(drop=True)

        col_map = {
            "학생A": "student_a", "학생B": "student_b",
            "관계유형": "type", "심각도": "severity", "메모": "note"
        }
        df = df.rename(columns=col_map)
        print(f"컬럼 변환 후: {list(df.columns)}")
        print(f"데이터 미리보기:\n{df.head()}")
        
        # 필요한 컬럼이 없으면 에러 방지
        required = ["student_a", "student_b", "type"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"필수 컬럼 없음: {missing}. 현재 컬럼: {list(df.columns)}")
        
        df = df.dropna(subset=["student_a", "student_b", "type"])
        # 빈 문자열도 제거
        df = df[df["student_a"].astype(str).str.strip() != ""]
        df = df[df["student_b"].astype(str).str.strip() != ""]
        df = df[df["student_a"].astype(str) != "nan"]
        df = df[df["student_b"].astype(str) != "nan"]

        results = []
        high_severity = []  # 심각도 높음인 항목만 따로 모음
        for _, row in df.iterrows():
            try:
                student_a = str(row.get("student_a", "") or "").strip()
                student_b = str(row.get("student_b", "") or "").strip()
                rel_type = str(row.get("type", "") or "").strip()
                
                # 빈 행 스킵
                if not student_a or not student_b or not rel_type:
                    continue
                if student_a == "nan" or student_b == "nan":
                    continue
                    
                severity_raw = row.get("severity", "중간")
                severity = str(severity_raw).strip() if severity_raw and str(severity_raw) != "nan" else "중간"
                note_raw = row.get("note", "")
                note = str(note_raw).strip() if note_raw and str(note_raw) != "nan" else None
                
                relation = {
                    "student_a": student_a,
                    "student_b": student_b,
                    "type": rel_type,
                    "severity": severity,
                    "note": note,
                }
                result = db.add_relation(relation)
                results.append(result)

                # 심각도 높음/중간 이상인 것 자동 조건으로 추가 (try 블록 안으로 이동)
                if severity in ["높음", "중간"]:
                    high_severity.append({
                        "type": "분리" if relation["type"] == "갈등" else "같은 반",
                        "student_a": relation["student_a"],
                        "student_b": relation["student_b"],
                        "note": relation["note"],
                        "severity": severity,
                        "relation_type": relation["type"],
                    })
            except Exception as row_err:
                print(f"행 처리 오류: {row_err}, 행: {dict(row)}")
                continue

        return {
            "success": True,
            "count": len(results),
            "relations": results,
            "auto_conditions": high_severity  # 프론트에서 자동 적용할 조건
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"파일 처리 오류: {str(e)}")


@app.get("/notes/status")
async def get_notes_status():
    """교사 소견 파싱 완료 여부 반환"""
    return _notes_parsing_status

@app.post("/notes/parse")
async def trigger_parse_notes(background_tasks: BackgroundTasks):
    """교사 소견 파싱 수동 실행"""
    students = db.get_all_students()
    if not students:
        raise HTTPException(status_code=404, detail="학생 데이터가 없습니다")
    
    notes_students = [s for s in students if s.get("teacher_note")]
    if not notes_students:
        raise HTTPException(status_code=404, detail="교사 소견이 있는 학생이 없습니다")
    
    background_tasks.add_task(parse_teacher_notes_bg, students)
    return {
        "message": f"파싱 시작: {len(notes_students)}명",
        "status": "processing"
    }

@app.get("/config/api-key")
async def get_api_key():
    """프론트에서 OpenRouter 직접 호출용 키 반환"""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return {"key": key}

@app.post("/student/reason")
async def get_student_reason(data: dict):
    """특정 학생의 반 배정 이유 AI 설명"""
    name = data.get("name", "")
    class_num = data.get("class_num", "")
    student = data.get("student", {})
    relations = data.get("relations", [])
    abs_conditions = data.get("abs_conditions", [])

    prompt = f"""학생 "{name}"이 {class_num}에 배정된 이유를 3-4문장으로 설명해주세요.

학생 정보: 성별={student.get('gender','?')}, 성적={student.get('academic_level','?')}, 특수교육={student.get('special_needs','없음')}, 교사소견={student.get('teacher_note','없음')}
관련 관계: {', '.join([f"{r.get('student_a')}↔{r.get('student_b')}({r.get('type')},{r.get('severity')})" for r in relations]) or '없음'}
적용된 조건: {', '.join([f"{c.get('type')}: {c.get('student_a')}↔{c.get('student_b')}" for c in abs_conditions]) or '없음'}

한국어로 교사에게 설명하듯 친절하고 구체적으로 답변해주세요."""

    try:
        reason = await ai._call([{"role": "user", "content": prompt}], max_tokens=300)
        return {"reason": reason}
    except Exception as e:
        return {"reason": f"설명 생성 오류: {str(e)}"}

@app.get("/students/template")
async def download_template():
    """학생 데이터 입력 Excel 템플릿 다운로드"""
    from fastapi.responses import FileResponse
    import os
    template_path = "student_template.xlsx"
    if os.path.exists(template_path):
        return FileResponse(
            template_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="Class_Twin_학생데이터_템플릿.xlsx"
        )
    raise HTTPException(status_code=404, detail="템플릿 파일이 없습니다")

frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")