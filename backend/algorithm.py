from typing import List, Dict, Any, Optional
import random
from itertools import combinations


class ClassAssigner:
    """
    반배정 최적화 알고리즘
    개선사항:
    1. gender/academic 점수 변수명 버그 수정
    2. 라운드로빈을 지그재그 방식으로 교체 (성적 쏠림 방지)
    3. 50번 랜덤 시도 → 결정론적 초기 배치 + 스왑 최적화
    4. 특수교육 학생 조건 실제 처리 추가
    """

    def __init__(self, students: List[Dict], relations: List[Dict], conditions: Dict, variant: int = 0):
        self.students = students
        self.relations = relations
        self.conditions = conditions
        self.variant = variant  # 0=A(최적), 1=B(차선), 2=C(균형만)
        if variant > 0:
            import random
            random.seed(variant * 42)
        self._conflict_weight = 1.0
        self._balance_weight = 1.0
        self._isolated_weight = 1.0

        self.conflict_pairs: set = set()
        self.friend_pairs: set = set()

        self.high_friend_pairs = set()  # 심각도 높음인 친함 관계만
        self.friend_count = {}  # 학생별 친한 친구 수

        for r in relations:
            a, b = r["student_a"], r["student_b"]
            if r["type"] == "갈등" and r.get("severity") in ["높음", "심각", "중간"]:
                self.conflict_pairs.add((min(a, b), max(a, b)))
            elif r["type"] == "친함":
                self.friend_pairs.add((min(a, b), max(a, b)))
                if r.get("severity") == "높음":
                    self.high_friend_pairs.add((min(a, b), max(a, b)))
                    self.friend_count[a] = self.friend_count.get(a, 0) + 1
                    self.friend_count[b] = self.friend_count.get(b, 0) + 1

        for cond in conditions.get("absolute", []):
            ctype = cond.get("type", "")
            # students 배열 우선 (3명 이상 지원), 없으면 a/b로 폴백
            students_list = cond.get("students", [])
            a = cond.get("student_a", "")
            b = cond.get("student_b", "")
            if not students_list:
                if a: students_list.append(a)
                if b: students_list.append(b)
            students_list = [s for s in students_list if s]

            if ctype == "분리":
                for i in range(len(students_list)):
                    for j in range(i+1, len(students_list)):
                        x, y = students_list[i], students_list[j]
                        self.conflict_pairs.add((min(x, y), max(x, y)))
            elif ctype == "같은 반":
                for i in range(len(students_list)):
                    for j in range(i+1, len(students_list)):
                        x, y = students_list[i], students_list[j]
                        self.friend_pairs.add((min(x, y), max(x, y)))

        # [개선 5] friend_pairs 전이 처리
        # A↔B, B↔C → A↔C 자동 추가 (같은 반 그룹핑)
        changed = True
        while changed:
            changed = False
            new_pairs = set()
            for (a, b) in self.friend_pairs:
                for (c, d) in self.friend_pairs:
                    for x, y in [(a, c), (a, d), (b, c), (b, d)]:
                        if x != y:
                            pair = (min(x, y), max(x, y))
                            if pair not in self.friend_pairs:
                                new_pairs.add(pair)
                                changed = True
            self.friend_pairs.update(new_pairs)

        # [개선 4] 특수교육 학생 이름 수집
        self.special_students: Dict[str, str] = {}  # name → special_needs
        for cond in conditions.get("absolute", []):
            if cond.get("type") == "특수 배려":
                name = cond.get("student_a", "")
                if name:
                    self.special_students[name] = "배려"
        for s in students:
            if s.get("special_needs"):
                self.special_students[s["name"]] = s["special_needs"]

    def assign(self, num_classes: int = 3) -> Dict:
        n = len(self.students)
        if n == 0:
            return {"error": "학생 데이터 없음"}

        # variant에 따라 완전히 다른 배치 전략 적용
        if self.variant == 0:
            # 배치안 A: 전체 균형 최적화 (기본) - 가장 많이 최적화
            classes = self._initial_assign(num_classes)
            classes = self._optimize_by_swap(classes, iterations=300)

        elif self.variant == 1:
            # 배치안 B: 친한 친구 같은 반 최우선 - 적게 최적화
            classes = self._assign_friends_first(num_classes)
            classes = self._optimize_by_swap(classes, iterations=30)

        else:
            # 배치안 C: 성적/성비 균등 최우선 - 적게 최적화
            classes = self._assign_balance_first(num_classes)
            classes = self._optimize_by_swap(classes, iterations=30)

        classes_output = {
            f"class_{i+1}": [s["name"] for s in cls]
            for i, cls in enumerate(classes)
        }
        stability = self.analyze_stability(classes)
        conditions_met = self._check_conditions(classes)
        raw_score = stability["total_score"]
        display_score = raw_score  # [개선] 가짜 보정 제거 — 점수는 항상 진짜

        # variant별 강점 라벨 (점수 깎는 대신 강점을 명시)
        variant_labels = {
            0: "균형 최적화",
            1: "친구관계 우선",
            2: "성적·성비 균등 우선",
        }
        variant_label = variant_labels.get(self.variant, "")

        # 고립 학생 이름 conditions_met에 추가
        isolated_names = stability.get("isolated_student_names", [])
        for name in isolated_names:
            conditions_met["unmet"].append(f"{name} — 친한 친구와 다른 반 배정 (고립 위험)")

        return {
            "classes": classes_output,
            "stability_score": display_score,
            "stability_detail": stability,
            "conditions_met": conditions_met,
            "total_students": n,
            "num_classes": num_classes,
            "variant_label": variant_label,
        }

    def _initial_assign(self, num_classes: int) -> List[List[Dict]]:
        """
        우선순위 기반 배치:
        1순위: 절대 조건 (분리/같은반) - 반드시 지킴
        2순위: 특수교육 학생 균등 분배
        3순위: 인원/성비/성적 균등
        4순위: 리더십 등 AI 특성 분산
        """
        classes = [[] for _ in range(num_classes)]
        placed = set()
        total = len(self.students)
        target_size = total // num_classes

        # ── 우선순위 배치 ──
        # 1순위: 같은반/분리 조건
        # 2순위: 특수교육 균등 분배
        # 3순위: 리더십 균등 분산
        # 4순위: 인원/성비 균등
        # 5순위: 성적 균등

        # ── 1순위: 절대 조건 그룹 배치 (Union-Find) ──
        groups = {}
        group_members = {}

        def find(x):
            if x not in groups:
                groups[x] = x
                group_members[x] = {x}
            if groups[x] != x:
                groups[x] = find(groups[x])
            return groups[x]

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx == ry: return
            for m in group_members[ry]: groups[m] = rx
            group_members[rx].update(group_members[ry])
            del group_members[ry]

        # 같은반 조건 Union-Find
        same_class_names = set()
        for cond in self.conditions.get("absolute", []):
            if cond.get("type") == "같은 반":
                members = cond.get("students", [])
                if not members:
                    a, b = cond.get("student_a",""), cond.get("student_b","")
                    members = [x for x in [a,b] if x]
                for n in members:
                    same_class_names.add(n)
                    find(n)
                for i in range(len(members)-1):
                    union(members[i], members[i+1])

        # 같은반 그룹 배치
        seen_roots = set()
        for name in same_class_names:
            root = find(name)
            if root in seen_roots: continue
            seen_roots.add(root)
            members = list(group_members[root])
            target = min(range(num_classes), key=lambda i: len(classes[i]))
            for m in members:
                if m not in placed:
                    s = next((st for st in self.students if st["name"] == m), None)
                    if s:
                        classes[target].append(s)
                        placed.add(m)

        # ── 2순위: 특수교육 학생 완전 균등 분배 (쿼터 기반) ──
        # 전체 특수교육 학생 수로 쿼터 계산
        all_special = [s for s in self.students if s.get("special_needs")]
        total_special = len(all_special)
        base_per_class = total_special // num_classes
        extra = total_special % num_classes
        special_quota = {i: base_per_class + (1 if i < extra else 0) for i in range(num_classes)}

        # 이미 배치된 특수교육 학생 카운트 (1순위 배치 포함)
        special_per_class = {i: 0 for i in range(num_classes)}
        for i, cls in enumerate(classes):
            for s in cls:
                if s.get("special_needs"):
                    special_per_class[i] += 1

        # 쿼터 초과된 반의 특수교육 학생을 부족한 반으로 이동
        # 단, 같은반 절대조건으로 묶인 쌍은 분리하지 않음
        same_class_pairs = set()
        for cond in self.conditions.get("absolute", []):
            if cond.get("type") == "같은 반":
                members = cond.get("students", [cond.get("student_a",""), cond.get("student_b","")])
                for m in members:
                    same_class_pairs.add(m)

        for i in range(num_classes):
            while special_per_class[i] > special_quota[i]:
                # 같은반 조건에 포함되지 않은 특수교육 학생만 이동
                excess = next(
                    (s for s in classes[i] if s.get("special_needs") and s["name"] not in same_class_pairs),
                    None
                )
                if not excess:
                    break  # 이동 가능한 학생 없으면 포기
                under = [j for j in range(num_classes) if special_per_class[j] < special_quota[j] and j != i]
                if not under:
                    break
                target = min(under, key=lambda j: len(classes[j]))
                classes[i].remove(excess)
                classes[target].append(excess)
                special_per_class[i] -= 1
                special_per_class[target] += 1

        # 미배치 특수교육 학생 배치
        special_students = [s for s in self.students if s["name"] not in placed and s.get("special_needs")]
        for s in special_students:
            under_quota = [i for i in range(num_classes) if special_per_class[i] < special_quota[i]]
            target = min(under_quota if under_quota else range(num_classes), key=lambda i: len(classes[i]))
            classes[target].append(s)
            placed.add(s["name"])
            special_per_class[target] += 1

        # ── 2.5순위: 고립 학생 (friend_pairs 없는 학생) 처리 ──
        # friend_pairs가 있는 그룹에 고립 학생을 같은 반에 배치
        paired_names = set()
        for a, b in self.friend_pairs:
            paired_names.add(a)
            paired_names.add(b)

        isolated = [s for s in self.students if s["name"] not in placed and s["name"] not in paired_names]
        non_isolated = [s for s in self.students if s["name"] not in placed and s["name"] in paired_names]

        # 고립 학생 (친구 없는 학생) 배치
        for s in isolated:
            candidates = [i for i in range(num_classes) if not self._has_conflict(s["name"], classes[i])]
            if not candidates:
                candidates = list(range(num_classes))
            target = min(candidates, key=lambda i: len(classes[i]))
            classes[target].append(s)
            placed.add(s["name"])

        # friend_pairs에 있지만 아직 미배치된 학생 처리
        for s in non_isolated:
            candidates = [i for i in range(num_classes) if not self._has_conflict(s["name"], classes[i])]
            if not candidates:
                candidates = list(range(num_classes))
            target = min(candidates, key=lambda i: len(classes[i]))
            classes[target].append(s)
            placed.add(s["name"])

        # ── 3순위: trait_balance 조건 학생 균등 분산 (리더십 기본 + 챗봇 요청 trait) ──
        # 기본: leadership >= 7
        trait_conditions = [{"trait": "leadership", "threshold": 7}]
        # 챗봇 조건에서 trait_balance 추가
        for cond in self.conditions.get("absolute", []):
            if cond.get("type") == "trait_balance" and cond.get("trait"):
                trait_conditions.append({
                    "trait": cond["trait"],
                    "threshold": cond.get("threshold", 7)
                })
        
        print(f"🎯 trait_balance 조건: {trait_conditions}")

        dispersed_names = set()
        for tc in trait_conditions:
            trait_key = tc["trait"]
            threshold = tc["threshold"]
            trait_students = sorted(
                [s for s in self.students if s["name"] not in placed
                 and s["name"] not in dispersed_names
                 and s.get("ai_traits", {}).get(trait_key, 0) >= threshold],
                key=lambda s: -s.get("ai_traits", {}).get(trait_key, 0)
            )
            print(f"  {trait_key} >= {threshold}: {len(trait_students)}명 - {[s['name'] for s in trait_students[:5]]}")
            for i, s in enumerate(trait_students):
                target = i % num_classes
                classes[target].append(s)
                placed.add(s["name"])
                dispersed_names.add(s["name"])

        # ── 4순위: 나머지 학생 성적/성별 균등 배분 ──
        remaining = [s for s in self.students if s["name"] not in placed]
        level_order = {"상": 0, "중": 1, "하": 2}
        remaining.sort(key=lambda s: (
            level_order.get(s.get("academic_level","중"), 1),
            s.get("gender","")
        ))
        for s in remaining:
            # 분리 조건 체크: 갈등 상대 없는 반 중 가장 적은 반
            candidates = [
                i for i in range(num_classes)
                if not self._has_conflict(s["name"], classes[i])
            ]
            if not candidates:
                candidates = list(range(num_classes))
            target = min(candidates, key=lambda i: len(classes[i]))
            classes[target].append(s)

        return classes

    def _assign_friends_first(self, num_classes: int) -> List[List[Dict]]:
        """배치안 B: 친한 친구 같은 반 최우선 배치"""
        classes = [[] for _ in range(num_classes)]
        placed = set()

        # Step 1: Union-Find로 friend 그룹 전체를 같은 반에
        groups = {}
        group_members = {}

        def find(x):
            if x not in groups: groups[x] = x; group_members[x] = {x}
            if groups[x] != x: groups[x] = find(groups[x])
            return groups[x]

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx == ry: return
            for m in group_members[ry]: groups[m] = rx
            group_members[rx].update(group_members[ry])
            del group_members[ry]

        all_names = set()
        for a, b in self.friend_pairs:
            all_names.add(a); all_names.add(b)
            find(a); find(b); union(a, b)

        # 그룹별로 같은 반에 배치 (가장 적은 반에)
        seen = set()
        for name in all_names:
            root = find(name)
            if root in seen: continue
            seen.add(root)
            members = list(group_members[root])
            target = min(range(num_classes), key=lambda i: len(classes[i]))
            for m in members:
                s = next((st for st in self.students if st["name"] == m), None)
                if s:
                    classes[target].append(s)
                    placed.add(m)

        # Step 2: 리더십 높은 학생 먼저 각 반에 균등 배치 (인원 균등 보장)
        remaining = [s for s in self.students if s["name"] not in placed]
        leaders = sorted(
            [s for s in remaining if s.get("ai_traits", {}).get("leadership", 0) >= 7],
            key=lambda s: -s.get("ai_traits", {}).get("leadership", 0)
        )
        for s in leaders:
            target = min(range(num_classes), key=lambda i: len(classes[i]))
            classes[target].append(s)
            placed.add(s["name"])

        # Step 3: 나머지 학생 성적/성별 균등 배분 (인원 균등 보장)
        remaining = [s for s in self.students if s["name"] not in placed]
        level_order = {"상": 0, "중": 1, "하": 2}
        remaining.sort(key=lambda s: (
            level_order.get(s.get("academic_level", "중"), 1),
            s.get("gender", "")
        ))
        total = len([s for cls in classes for s in cls]) + len(remaining)
        target_size = total // num_classes
        for s in remaining:
            # 인원이 목표치 미만인 반 중에서 선택
            under = [i for i in range(num_classes) if len(classes[i]) < target_size + 1]
            target = min(under if under else range(num_classes), key=lambda i: len(classes[i]))
            classes[target].append(s)

        return classes

    def _assign_balance_first(self, num_classes: int) -> List[List[Dict]]:
        """배치안 C: 성적/성비 완벽 균등 최우선 (friend_pairs 무시)"""
        classes = [[] for _ in range(num_classes)]

        # 성별·성적으로 정렬 후 라운드로빈
        male_upper = [s for s in self.students if s.get("gender")=="남" and s.get("academic_level")=="상"]
        male_mid   = [s for s in self.students if s.get("gender")=="남" and s.get("academic_level")=="중"]
        male_lower = [s for s in self.students if s.get("gender")=="남" and s.get("academic_level")=="하"]
        fem_upper  = [s for s in self.students if s.get("gender")=="여" and s.get("academic_level")=="상"]
        fem_mid    = [s for s in self.students if s.get("gender")=="여" and s.get("academic_level")=="중"]
        fem_lower  = [s for s in self.students if s.get("gender")=="여" and s.get("academic_level")=="하"]

        # 각 그룹을 교대로 반에 배분 (가장 작은 반부터 채워서 인원 균등 유지)
        for group in [male_upper, fem_upper, male_mid, fem_mid, male_lower, fem_lower]:
            for s in group:
                target = min(range(num_classes), key=lambda k: len(classes[k]))
                classes[target].append(s)

        # [개선] gender가 비어있거나 "남/여"가 아닌 학생, 또는 academic_level이
        # "상/중/하"가 아닌 학생은 위 6개 그룹에서 누락됨 → 라운드로빈으로 추가 배분
        assigned_names = {s["name"] for cls in classes for s in cls}
        leftover = [s for s in self.students if s["name"] not in assigned_names]
        for i, s in enumerate(leftover):
            # 가장 인원이 적은 반에 추가 (균형 유지)
            target = min(range(num_classes), key=lambda k: len(classes[k]))
            classes[target].append(s)

        return classes

    def _optimize_by_swap(self, classes: List[List[Dict]], iterations: int = 200) -> List[List[Dict]]:
        """
        [개선] Simulated Annealing 기반 스왑 최적화
        - 초반에는 나빠지는 swap도 확률적으로 수용 → local optimum 탈출
        - 최고 점수 배치를 별도 보관 → 어떤 경우에도 손해 없음
        - 갈등/고립 학생을 후보로 우선 선택 → 효율 ↑
        """
        import math, copy

        current_score = self._calc_stability_score(classes)
        best_score = current_score
        best_classes = copy.deepcopy(classes)

        # 초기/최종 온도 (점수 스케일 100점 기준)
        T0, T1 = 8.0, 0.1

        # "문제 학생" 후보군 수집 — 갈등 관계 또는 고립
        problem_names = set()
        for (a, b) in self.conflict_pairs:
            problem_names.add(a); problem_names.add(b)
        for name in self.get_isolated_students(classes) or []:
            problem_names.add(name)

        for it in range(iterations):
            T = T0 * ((T1 / T0) ** (it / max(1, iterations - 1)))

            c1, c2 = random.sample(range(len(classes)), 2)
            if not classes[c1] or not classes[c2]:
                continue

            # 30% 확률로 문제 학생을 c1 후보에서 우선 고름
            i1 = None
            if problem_names and random.random() < 0.3:
                cands = [k for k, s in enumerate(classes[c1]) if s["name"] in problem_names]
                if cands:
                    i1 = random.choice(cands)
            if i1 is None:
                i1 = random.randrange(len(classes[c1]))
            i2 = random.randrange(len(classes[c2]))

            classes[c1][i1], classes[c2][i2] = classes[c2][i2], classes[c1][i1]

            # 하드 제약 위반 시 즉시 롤백
            if self._violates_absolute(classes) or self._violates_special_quota(classes):
                classes[c1][i1], classes[c2][i2] = classes[c2][i2], classes[c1][i1]
                continue
            sizes = [len(cls) for cls in classes]
            if max(sizes) - min(sizes) > 1:
                classes[c1][i1], classes[c2][i2] = classes[c2][i2], classes[c1][i1]
                continue

            new_score = self._calc_stability_score(classes)
            delta = new_score - current_score

            # 수용 여부: 좋아지면 무조건, 나빠져도 exp(delta/T) 확률로
            if delta >= 0 or random.random() < math.exp(delta / T):
                current_score = new_score
                if new_score > best_score:
                    best_score = new_score
                    best_classes = copy.deepcopy(classes)
            else:
                classes[c1][i1], classes[c2][i2] = classes[c2][i2], classes[c1][i1]

        return best_classes

    def _has_conflict(self, name: str, class_students: List[Dict]) -> bool:
        for s in class_students:
            pair = (min(name, s["name"]), max(name, s["name"]))
            if pair in self.conflict_pairs:
                return True
        return False

    def _fallback_assign(self, num_classes: int) -> List[List[Dict]]:
        students = self.students.copy()
        random.shuffle(students)
        classes = [[] for _ in range(num_classes)]
        for i, s in enumerate(students):
            classes[i % num_classes].append(s)
        return classes

    def analyze_stability(self, classes) -> Dict:
        if isinstance(classes, dict):
            name_map = {s["name"]: s for s in self.students}
            classes = [
                [name_map[n] for n in classes[key] if n in name_map]
                for key in sorted(classes.keys())
            ]

        scores = {}

        # 1. 갈등 쌍 점수 (30점 - 비중 조정)
        conflict_count = 0
        for cls in classes:
            names = [s["name"] for s in cls]
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    pair = (min(names[i], names[j]), max(names[i], names[j]))
                    if pair in self.conflict_pairs:
                        conflict_count += 1
        conflict_score = max(0, 30 - conflict_count * 15)
        scores["conflict"] = {"score": conflict_score, "max": 30}

        # 2. 성비/성적 균등 점수 (각 20점) - balance 우선순위 반영
        balance = self.conditions.get("balance", [])
        # 우선순위 낮을수록(숫자 클수록) 가중치 낮음
        def get_weight(label_keywords, default=1.0):
            for b in balance:
                lbl = b.get("label", "")
                if any(k in lbl for k in label_keywords):
                    pri = b.get("priority", 3)
                    # 1순위=1.3, 2순위=1.1, 3순위=1.0, 4순위=0.9, 5순위=0.8
                    return max(0.6, 1.5 - pri * 0.1)
            return default

        gender_w = get_weight(["성비"])
        academic_w = get_weight(["성적"])
        leader_w = get_weight(["리더십"])

        scores["gender"] = {"score": round(self._calc_gender_score(classes) * 20 * gender_w), "max": 20}
        scores["academic"] = {"score": round(self._calc_academic_score(classes) * 20 * academic_w), "max": 20}

        # 3. 고립 학생 점수 (15점)
        isolated_count = self._count_isolated(classes)
        scores["isolated"] = {"score": max(0, 15 - isolated_count * 8), "max": 15}

        # 4. [신규] AI 분석 특성 균등 점수 (15점)
        # 리더십이나 긍정적 영향력이 한 반에 쏠리지 않도록 계산
        ai_score = self._calc_ai_trait_score(classes) * 15
        scores["ai_analysis"] = {"score": round(ai_score), "max": 15}

        total = min(100, sum(s["score"] for s in scores.values()))
        isolated_names = self.get_isolated_students(classes) if hasattr(self, 'get_isolated_students') else []
        return {
            "total_score": round(total),
            "detail": scores,
            "conflict_pairs_in_same_class": conflict_count,
            "isolated_students": isolated_count,
            "isolated_student_names": isolated_names,
        }

    def _calc_ai_trait_score(self, classes) -> float:
        """AI 분석 특성(리더십 + 학업성취 + 사회성)을 반별 균등 분산 측정"""

        def trait_variance_score(trait_key, default=5):
            class_sums = []
            for cls in classes:
                total = sum(s.get("ai_traits", {}).get(trait_key, default) for s in cls)
                class_sums.append(total)
            if not class_sums or sum(class_sums) == 0:
                return 1.0
            avg = sum(class_sums) / len(class_sums)
            variance = sum((s - avg) ** 2 for s in class_sums) / len(class_sums)
            return max(0.0, 1 - (variance / (avg**2 + 1)) * 5)

        # 리더십, 학업성취, 사회성 균등 분산 점수 평균
        leadership_score = trait_variance_score("leadership")
        academic_score   = trait_variance_score("attention")   # 집중력/학업성취
        social_score     = trait_variance_score("sociability")  # 사회성

        return (leadership_score + academic_score + social_score) / 3

    def _calc_stability_score(self, classes) -> float:
        """variant + balance 우선순위 가중치 적용한 점수 계산 (최적화용)"""
        if isinstance(classes, dict):
            name_map = {s["name"]: s for s in self.students}
            classes_list = [
                [name_map[n] for n in classes[key] if n in name_map]
                for key in sorted(classes.keys())
            ]
        else:
            classes_list = classes

        # balance 우선순위 가중치
        balance = self.conditions.get("balance", [])
        def get_bw(keywords):
            for b in balance:
                if any(k in b.get("label","") for k in keywords):
                    pri = b.get("priority", 3)
                    return max(0.5, 1.6 - pri * 0.1)
            return 1.0

        gender_w  = get_bw(["성비"]) * self._balance_weight
        academic_w= get_bw(["성적"]) * self._balance_weight

        # 갈등 쌍 점수
        conflict_count = 0
        for cls in classes_list:
            names = [s["name"] for s in cls]
            for i in range(len(names)):
                for j in range(i+1, len(names)):
                    pair = (min(names[i], names[j]), max(names[i], names[j]))
                    if pair in self.conflict_pairs:
                        conflict_count += 1
        conflict_score = max(0, 30 - conflict_count * 15) * self._conflict_weight

        # 균형 점수 (우선순위 가중치 적용)
        gender_score   = self._calc_gender_score(classes_list) * 20 * gender_w
        academic_score = self._calc_academic_score(classes_list) * 20 * academic_w

        # 고립 점수
        isolated_count = self._count_isolated(classes_list)
        isolated_score = max(0, 15 - isolated_count * 8) * self._isolated_weight

        # 인원 균등 패널티 (반별 인원 차이가 클수록 감점)
        sizes = [len(cls) for cls in classes_list if cls]
        if sizes:
            avg_size = sum(sizes) / len(sizes)
            size_variance = sum(abs(s - avg_size) for s in sizes) / len(sizes)
            size_penalty = min(10, size_variance * 3)  # 최대 10점 감점
        else:
            size_penalty = 0

        return max(0, conflict_score + gender_score + academic_score + isolated_score - size_penalty)

    def _calc_gender_score(self, classes) -> float:
        ratios = []
        for cls in classes:
            if not cls:
                continue
            male = sum(1 for s in cls if s.get("gender") == "남")
            ratios.append(male / len(cls))
        if not ratios:
            return 1.0
        avg = sum(ratios) / len(ratios)
        variance = sum((r - avg) ** 2 for r in ratios) / len(ratios)
        return max(0.0, 1 - variance * 10)

    def _calc_academic_score(self, classes) -> float:
        levels = {"상": 0, "중": 1, "하": 2}
        class_avgs = []
        for cls in classes:
            if not cls:
                continue
            avg = sum(levels.get(s.get("academic_level", "중"), 1) for s in cls) / len(cls)
            class_avgs.append(avg)
        if not class_avgs:
            return 1.0
        overall_avg = sum(class_avgs) / len(class_avgs)
        variance = sum((a - overall_avg) ** 2 for a in class_avgs) / len(class_avgs)
        return max(0.0, 1 - variance * 5)

    def _count_isolated(self, classes) -> int:
        """
        고립 학생 수 계산:
        - 심각도 높음인 친함 관계에서 다른 반에 배치된 경우
        - 그 친구가 유일한 친한 친구면 해당 학생만 고립으로 카운트
        - 둘 다 다른 친구 없으면 둘 다 고립
        """
        name_to_class = {}
        for i, cls in enumerate(classes):
            for s in cls:
                name_to_class[s["name"]] = i

        isolated_set = set()

        for a, b in self.high_friend_pairs:
            ca = name_to_class.get(a, -1)
            cb = name_to_class.get(b, -1)
            if ca == cb or ca == -1 or cb == -1:
                continue  # 같은 반이면 고립 아님

            # 다른 반에 배치됨 → 각자 친한 친구가 유일한지 확인
            a_only = self.friend_count.get(a, 0) <= 1  # a의 유일한 친구가 b
            b_only = self.friend_count.get(b, 0) <= 1  # b의 유일한 친구가 a

            if a_only:
                isolated_set.add(a)
            if b_only:
                isolated_set.add(b)

        return len(isolated_set)

    def get_isolated_students(self, classes) -> list:
        """고립 학생 이름 목록 반환"""
        name_to_class = {}
        for i, cls in enumerate(classes):
            for s in cls:
                name_to_class[s["name"]] = i

        isolated_set = set()
        for a, b in self.high_friend_pairs:
            ca = name_to_class.get(a, -1)
            cb = name_to_class.get(b, -1)
            if ca == cb or ca == -1 or cb == -1:
                continue
            if self.friend_count.get(a, 0) <= 1:
                isolated_set.add(a)
            if self.friend_count.get(b, 0) <= 1:
                isolated_set.add(b)
        return list(isolated_set)

    def _violates_special_quota(self, classes) -> bool:
        """특수교육 학생 반별 분배가 쿼터 범위 벗어나면 위반"""
        total_special = sum(1 for s in self.students if s.get("special_needs"))
        if total_special == 0:
            return False
        n = len(classes)
        base = total_special // n
        remainder = total_special % n
        min_allowed = base
        max_allowed = base + (1 if remainder > 0 else 0)

        # 같은반 절대조건으로 묶인 특수교육 학생은 max 제한 완화
        same_class_special = set()
        for cond in self.conditions.get("absolute", []):
            if cond.get("type") == "같은 반":
                members = cond.get("students", [cond.get("student_a",""), cond.get("student_b","")])
                for m in members:
                    s = next((st for st in self.students if st["name"] == m and st.get("special_needs")), None)
                    if s:
                        same_class_special.add(m)

        for cls in classes:
            if not cls:
                continue
            sp_count = sum(1 for s in cls if s.get("special_needs"))
            # 같은반 조건 특수교육 포함 시 max 1 더 허용
            extra = sum(1 for s in cls if s.get("special_needs") and s["name"] in same_class_special)
            effective_max = max_allowed + min(extra, 2)
            if sp_count > effective_max:
                return True
            if sp_count < min_allowed:
                return True
        return False

    def _violates_absolute(self, classes) -> bool:
        """절대 조건 위반 여부 체크 (스왑 최적화 중 사용)"""
        name_to_class = {}
        for i, cls in enumerate(classes):
            for s in cls:
                name_to_class[s["name"]] = i

        for cond in self.conditions.get("absolute", []):
            students_list = cond.get("students", [])
            a = cond.get("student_a", "")
            b = cond.get("student_b", "")
            if not students_list:
                if a: students_list = [a]
                if b: students_list.append(b)

            ctype = cond.get("type", "")
            valid_names = [n for n in students_list if n in name_to_class]

            if ctype == "분리" and len(valid_names) >= 2:
                # 분리 조건: 같은 반이면 위반
                classes_of = [name_to_class[n] for n in valid_names]
                if len(set(classes_of)) < len(classes_of):
                    return True
            elif ctype == "같은 반" and len(valid_names) >= 2:
                # 같은 반 조건: 다른 반이면 위반
                classes_of = [name_to_class[n] for n in valid_names]
                if len(set(classes_of)) > 1:
                    return True

        return False

    def _check_conditions(self, classes) -> Dict:
        met, unmet = [], []
        seen = set()  # 중복 방지

        name_to_class = {}
        for i, cls in enumerate(classes):
            for s in cls:
                name_to_class[s["name"]] = i

        for cond in self.conditions.get("absolute", []):
            ctype = cond.get("type", "")
            students_list = cond.get("students", [])
            a = cond.get("student_a", "")
            b = cond.get("student_b", "")
            if not students_list:
                students_list = [x for x in [a, b] if x]

            if len(students_list) < 2:
                continue

            # 대표 키로 중복 방지
            key = (ctype, tuple(sorted(students_list[:2])))
            if key in seen:
                continue
            seen.add(key)

            na, nb = students_list[0], students_list[1]
            ca = name_to_class.get(na, -1)
            cb = name_to_class.get(nb, -1)

            if ctype == "분리":
                if ca != cb and ca != -1 and cb != -1:
                    met.append(f"{na} ↔ {nb} 분리 완료")
                else:
                    unmet.append(f"{na} ↔ {nb} 분리 실패")
            elif ctype == "같은 반":
                if ca == cb and ca != -1:
                    met.append(f"{na} ↔ {nb} 같은 반 배정 완료")
                else:
                    unmet.append(f"{na} ↔ {nb} 같은 반 배정 실패")

        # [개선 4] 특수교육 학생 배치 확인 — 반 번호 기록
        for name, stype in self.special_students.items():
            cls_num = next((i + 1 for i, cls in enumerate(classes) if any(s["name"] == name for s in cls)), None)
            if cls_num:
                met.append(f"{name} ({stype}) {cls_num}반 특수 배려 배치 완료")

        return {"met": met, "unmet": unmet, "total": len(met) + len(unmet), "met_count": len(met)}


# =============================================================


class SeatAssigner:
    """
    자리배정 알고리즘
    개선사항:
    5. 자리 배치 루프 버그 수정 (덮어쓰기 방지)
    6. 형평성 보정을 구석뿐 아니라 창가/앞줄 이력까지 반영
    7. _calc_equity_score 실제 이력 기반 계산으로 교체
    """

    def __init__(self, students: List[Dict], relations: List[Dict], conditions: Dict, seat_history: List[Dict]):
        self.students = students
        self.relations = relations
        self.conditions = conditions
        self.seat_history = seat_history

        self.conflict_pairs: set = set()
        self.friend_pairs: set = set()
        self.adjusted_students: set = set()  # _apply_equity로 자리가 옮겨진 학생
        for r in relations:
            a, b = r["student_a"], r["student_b"]
            if r["type"] == "갈등":
                self.conflict_pairs.add((min(a, b), max(a, b)))
            elif r["type"] == "친함":
                self.friend_pairs.add((min(a, b), max(a, b)))

        # 절대 조건에서 추가 분리 처리 + 위치 강제 조건 수집
        self.forced_front: set = set()       # 앞 2줄 강제 배치
        self.forced_teacher_near: set = set()  # 교사 근처 강제 배치
        self.forced_adjacent: set = set()     # 런타임 "옆자리/같이" 요청 (강한 친함)
        for cond in conditions.get("absolute", []):
            ctype = cond.get("type", "")
            if ctype in ["분리", "갈등"]:
                a = cond.get("student_a", "")
                b = cond.get("student_b", "")
                if a and b:
                    self.conflict_pairs.add((min(a, b), max(a, b)))
            elif ctype in ["앞자리 배치", "앞자리"]:
                name = cond.get("student_a") or cond.get("student", "")
                if name:
                    self.forced_front.add(name)
            elif ctype in ["교사 근처", "교사근처"]:
                name = cond.get("student_a") or cond.get("student", "")
                if name:
                    self.forced_teacher_near.add(name)
            elif ctype in ["같이", "같이 앉기", "옆자리", "같은 반"]:
                # 자리배정 context에서 "같은 반"은 무의미하므로 "옆자리"로 재해석
                a = cond.get("student_a", "")
                b = cond.get("student_b", "")
                if a and b:
                    pair = (min(a, b), max(a, b))
                    self.friend_pairs.add(pair)
                    self.forced_adjacent.add(pair)

        # balance 우선순위를 점수 가중치로 변환
        # 1순위 = 2.5, 2순위 = 1.5, 3순위 = 1.0, 4순위 = 0.7, 5순위 = 0.5
        # 1순위와 2순위 차이를 크게 벌려서 "1순위는 확실히 우선"을 보장
        self.balance_weights: Dict[str, float] = {}
        for b in conditions.get("balance", []) or []:
            label = b.get("label", "")
            pri = b.get("priority", 3)
            w = {1: 2.5, 2: 1.5, 3: 1.0, 4: 0.7, 5: 0.5}.get(pri, 1.0)
            # label 키워드 → 내부 키 매핑
            if "시력" in label:
                self.balance_weights["vision"] = w
            elif "특수" in label or "ADHD" in label or "학습" in label:
                self.balance_weights["special"] = w
            elif "키" in label:
                self.balance_weights["height"] = w
            elif "성별" in label:
                self.balance_weights["gender_alt"] = w
            elif "주의력" in label:
                self.balance_weights["attention"] = w
            elif "친한" in label or "친구" in label:
                self.balance_weights["friend_separate"] = w
            elif "형평" in label:
                self.balance_weights["equity"] = w
        # 기본값
        for k in ["vision", "special", "height", "gender_alt", "attention", "friend_separate", "equity"]:
            self.balance_weights.setdefault(k, 1.0)

        # 이름 → 이력 빠른 조회
        self.history_map: Dict[str, Dict] = {h["student_name"]: h for h in seat_history}

    def assign(self, rows: int = 5, cols: int = 6) -> Dict:
        seats: Dict[tuple, str] = {}

        vision_weak = [s for s in self.students if s.get("vision") == "약함"]
        adhd = [s for s in self.students if s.get("special_needs") == "ADHD"]
        # [신규] 절대조건의 "앞자리 배치" 학생도 우선 배치 대상
        forced_front_students = [s for s in self.students if s["name"] in self.forced_front]
        # 중복 제거 (우선순위: forced > vision > adhd)
        seen = set()
        priority_students = []
        for s in forced_front_students + vision_weak + adhd:
            if s["name"] not in seen:
                priority_students.append(s)
                seen.add(s["name"])
        priority_names = {s["name"] for s in priority_students}
        # 키 작은 순으로 정렬
        normal = sorted(
            [s for s in self.students if s["name"] not in priority_names],
            key=lambda s: s.get("height", 160),
        )

        # [개선] 우선 학생을 앞 2줄까지 흘려서 배치 (앞자리 부족 시 일반 큐로 회수)
        front_slots = [(r, c) for r in range(min(2, rows)) for c in range(cols)]
        spillover = []  # 앞자리에 못 들어간 priority 학생
        for student in priority_students:
            if not front_slots:
                spillover.append(student)
                continue
            pos = front_slots.pop(0)
            seats[pos] = student["name"]
        # 못 들어간 priority 학생은 normal 큐 앞에 다시 넣어서 어디든 배치되게 함
        if spillover:
            normal = spillover + normal

        # [개선] 나머지 학생도 앞→뒤 순서로 (키 작은 순) 채움
        all_slots = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in seats]
        for student in normal:
            placed = False
            for pos in all_slots:
                if pos in seats:
                    continue
                if not self._check_adjacent_conflict(student["name"], seats, pos[0], pos[1]):
                    seats[pos] = student["name"]
                    placed = True
                    break
            if not placed:
                for pos in all_slots:
                    if pos not in seats:
                        seats[pos] = student["name"]
                        break

        # [개선] 형평성 보정 (보정 후 갈등 재검사 포함)
        seats = self._apply_equity(seats, rows, cols)

        # [신규] Simulated Annealing 기반 swap 최적화
        seats = self._optimize_seats(seats, rows, cols, iterations=2000)

        # 결과 포맷
        seat_grid = []
        for r in range(rows):
            row_data = []
            for c in range(cols):
                name = seats.get((r, c))
                if name:
                    student = next((s for s in self.students if s["name"] == name), {})
                    # 셀별 분류 플래그 (동시 적용 가능)
                    flags = []
                    if student.get("vision") == "약함":
                        flags.append("시력배려")
                    if student.get("special_needs") and student.get("special_needs") not in ("없음", "일반", None):
                        flags.append("특수교육")
                    if name in self.adjusted_students:
                        flags.append("보정배치")
                    # 갈등 페어에 등장하는 학생은 인접 여부와 무관하게 갈등주의 표시
                    in_conflict = any(name in pair for pair in self.conflict_pairs)
                    if in_conflict:
                        flags.append("갈등주의")
                    row_data.append({
                        "row": r, "col": c,
                        "name": name,
                        "seat_num": r * cols + c + 1,
                        "gender": student.get("gender"),
                        "grade": student.get("academic_level"),
                        "special": self._get_special_type(student),
                        "special_needs": student.get("special_needs"),
                        "flags": flags,
                        "tag": self._get_tag(student),
                    })
                else:
                    row_data.append({"row": r, "col": c, "name": None, "seat_num": r * cols + c + 1})
            seat_grid.append(row_data)

        return {
            "seat_grid": seat_grid,
            "equity_score": self._calc_equity_score(seats, rows, cols),
            "conflict_adjacent_pairs": self._count_adjacent_conflicts(seats, rows, cols),
            "alerts": self._generate_alerts(seats, rows, cols),
            "rows": rows,
            "cols": cols,
        }

    def _check_adjacent_conflict(self, name: str, seats: Dict, row: int, col: int) -> bool:
        for r2, c2 in [(row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)]:
            neighbor = seats.get((r2, c2))
            if neighbor:
                pair = (min(name, neighbor), max(name, neighbor))
                if pair in self.conflict_pairs:
                    return True
        return False

    def _classify_seat(self, pos: tuple, rows: int, cols: int) -> str:
        """자리 유형 분류: 구석/창가/앞줄/뒷줄/중앙"""
        r, c = pos
        corner = {(0, 0), (0, cols - 1), (rows - 1, 0), (rows - 1, cols - 1)}
        if pos in corner:
            return "구석"
        if c == 0 or c == cols - 1:
            return "창가"
        if r == 0:
            return "앞줄"
        if r == rows - 1:
            return "뒷줄"
        return "중앙"

    def _apply_equity(self, seats: Dict, rows: int, cols: int) -> Dict:
        """
        [개선 6] 형평성 보정 확장
        - 구석 3회 이상: 중앙으로 이동
        - 앞줄 3회 이상: 뒷줄 빈 자리로 이동
        - 창가 3회 이상: 내부 열로 이동
        """
        for student_name, hist in self.history_map.items():
            seat_type = hist.get("seat_type", "")
            count = hist.get("corner_count", 0)
            if count < 3:
                continue

            current_pos = next((pos for pos, n in seats.items() if n == student_name), None)
            if current_pos is None:
                continue

            current_type = self._classify_seat(current_pos, rows, cols)
            if current_type != seat_type:
                continue

            target_type_map = {"구석": "중앙", "창가": "중앙", "앞줄": "뒷줄"}
            target_type = target_type_map.get(seat_type)
            if not target_type:
                continue

            for r in range(rows):
                for c in range(cols):
                    pos = (r, c)
                    if pos in seats:
                        continue
                    if self._classify_seat(pos, rows, cols) == target_type:
                        # [개선] 옮긴 자리가 갈등 인접인지 재검사
                        if self._check_adjacent_conflict(student_name, seats, pos[0], pos[1]):
                            continue
                        seats[pos] = student_name
                        del seats[current_pos]
                        self.adjusted_students.add(student_name)
                        break
                else:
                    continue
                break

        return seats

    def _get_special_type(self, student: Dict) -> str:
        if student.get("special_needs") == "ADHD":
            return "ADHD"
        if student.get("vision") == "약함":
            return "시력배려"
        return "일반"

    def _get_tag(self, student: Dict) -> str:
        return f"성적 {student.get('academic_level', '중')}"

    def _calc_equity_score(self, seats: Dict, rows: int, cols: int) -> int:
        """
        [개선 7] 실제 이력 기반 형평성 점수 계산
        자리 유형별 배분이 얼마나 균등한지 0~100 반환
        """
        if not self.seat_history:
            return 100

        type_counts: Dict[str, int] = {"구석": 0, "창가": 0, "앞줄": 0, "뒷줄": 0, "중앙": 0}
        for hist in self.seat_history:
            stype = hist.get("seat_type")
            if stype in type_counts:
                type_counts[stype] += 1

        total_records = sum(type_counts.values())
        if total_records == 0:
            return 100

        n_types = len([v for v in type_counts.values() if v > 0])
        if n_types == 0:
            return 100

        expected = total_records / n_types
        variance = sum((v - expected) ** 2 for v in type_counts.values() if v > 0) / n_types
        return max(0, 100 - int(variance ** 0.5 * 5))

    def _count_adjacent_conflicts(self, seats: Dict, rows: int, cols: int) -> int:
        count = 0
        checked: set = set()
        for (r, c), name in seats.items():
            for r2, c2 in [(r, c + 1), (r + 1, c)]:
                neighbor = seats.get((r2, c2))
                if neighbor:
                    pair = (min(name, neighbor), max(name, neighbor))
                    if pair in self.conflict_pairs and pair not in checked:
                        count += 1
                        checked.add(pair)
        return count

    def _generate_alerts(self, seats: Dict, rows: int, cols: int) -> List[Dict]:
        alerts = []
        for hist in self.seat_history:
            if hist.get("corner_count", 0) >= 3:
                alerts.append({
                    "type": "warn",
                    "message": f"{hist['student_name']} — {hist['corner_count']}개월 연속 {hist.get('seat_type', '구석')}자리 → 보정 배치 적용",
                })
        conflict_count = self._count_adjacent_conflicts(seats, rows, cols)
        if conflict_count == 0:
            alerts.append({"type": "success", "message": "갈등 쌍 전원 분리 완료"})
        else:
            alerts.append({"type": "danger", "message": f"갈등 인접 쌍 {conflict_count}개 발견"})
        return alerts

    # ============ [신규] 점수 함수 + SA 최적화 ============
    def _score_seats(self, seats: Dict, rows: int, cols: int) -> float:
        """배치 품질 점수 (높을수록 좋음). balance 우선순위가 가중치로 반영됨."""
        score = 0.0
        student_map = {s["name"]: s for s in self.students}
        bw = self.balance_weights

        # 1) 갈등 인접 (강한 페널티, 가중치 무관)
        for (r, c), name in seats.items():
            for r2, c2 in [(r, c+1), (r+1, c)]:
                neighbor = seats.get((r2, c2))
                if neighbor:
                    pair = (min(name, neighbor), max(name, neighbor))
                    if pair in self.conflict_pairs:
                        score -= 100

        # 2) 시력약함 페널티 — 뒤로 갈수록 점진적으로 커짐
        # 앞 2줄 안이면 0, 3행 -50×bw, 4행 -100×bw, 5행 -150×bw ...
        # 어쩔 수 없이 밀려나는 학생도 최대한 앞쪽(3행)으로 가게 유도
        for (r, c), name in seats.items():
            stu = student_map.get(name, {})
            if stu.get("vision") == "약함" and r >= 2:
                score -= 50 * (r - 1) * bw["vision"]

        # 2-1) 특수교육(ADHD/학습장애) 페널티 — 동일한 점진 페널티
        for (r, c), name in seats.items():
            stu = student_map.get(name, {})
            sn = stu.get("special_needs") or ""
            if sn and sn not in ("없음", "일반") and r >= 2:
                score -= 50 * (r - 1) * bw["special"]

        # 2-2) 런타임 forced_front (extra_note로 "앞자리" 요청된 학생)은 여전히 강하게
        for (r, c), name in seats.items():
            if name in self.forced_front and r >= 2:
                score -= 60  # 사용자가 명시한 요청이므로 강한 페널티

        # 2-1) [신규] 교사 근처 강제 배치 (교사가 앞 중앙이라 가정 → row 0, 중앙 cols)
        teacher_cols = {cols // 2 - 1, cols // 2} if cols >= 2 else {0}
        for (r, c), name in seats.items():
            if name in self.forced_teacher_near:
                if not (r == 0 and c in teacher_cols):
                    score -= 50

        # 3) 키 순서: 뒷줄일수록 키 큰 학생 선호 (가중치가 1순위면 강하게)
        for (r, c), name in seats.items():
            h = student_map.get(name, {}).get("height", 160)
            ideal = 150 + (r / max(1, rows - 1)) * 25
            score -= abs(h - ideal) * 1.0 * bw["height"]

        # 3-1) [신규] 성별 교차 배치 — 같은 성별이 좌우 인접하면 페널티
        for (r, c), name in seats.items():
            g = student_map.get(name, {}).get("gender", "")
            if not g:
                continue
            right = seats.get((r, c+1))
            if right and student_map.get(right, {}).get("gender") == g:
                score -= 3 * bw["gender_alt"]

        # 4) 친한 친구 인접 — 우선순위에 따라 부호가 바뀜
        friend_sign = -1 if bw["friend_separate"] >= 1.3 else 1
        for (r, c), name in seats.items():
            for r2, c2 in [(r, c+1), (r+1, c)]:
                neighbor = seats.get((r2, c2))
                if neighbor:
                    pair = (min(name, neighbor), max(name, neighbor))
                    if pair in self.friend_pairs:
                        score += friend_sign * 5 * bw["friend_separate"]

        # 4-1) [신규] 런타임 "옆자리" 강제 요청 — 매우 강한 점수 (균형조건 모두 이김)
        #     "옆자리"는 좌우(같은 행, 열 차이 1)만 인정.
        #     사용자의 명시적 의도이므로 시력배려 1순위(-60)도 이길 수 있는 크기로 설정.
        for pair in self.forced_adjacent:
            a, b = pair
            pa = next((p for p, n in seats.items() if n == a), None)
            pb = next((p for p, n in seats.items() if n == b), None)
            if pa and pb:
                same_row = pa[0] == pb[0]
                col_diff = abs(pa[1] - pb[1])
                row_diff = abs(pa[0] - pb[0])
                if same_row and col_diff == 1:
                    score += 250  # 시력 1명 희생(-60)보다 훨씬 큼
                elif same_row and col_diff == 2:
                    score += 50
                else:
                    score -= 40 * col_diff + 60 * row_diff

        # 5) 형평성 위반
        for name, hist in self.history_map.items():
            if hist.get("corner_count", 0) < 3:
                continue
            seat_type = hist.get("seat_type", "")
            pos = next((p for p, n in seats.items() if n == name), None)
            if pos and self._classify_seat(pos, rows, cols) == seat_type:
                score -= 20 * bw["equity"]

        return score

    def _optimize_seats(self, seats: Dict, rows: int, cols: int, iterations: int = 2000) -> Dict:
        """
        Simulated Annealing 기반 자리 swap 최적화.
        30명 기준 2000회는 0.1초 내외.
        """
        import math, random as _r, copy

        current = dict(seats)
        current_score = self._score_seats(current, rows, cols)
        best = dict(current)
        best_score = current_score

        positions = [(r, c) for r in range(rows) for c in range(cols) if (r, c) in current]
        T0, T1 = 30.0, 0.5

        for it in range(iterations):
            T = T0 * ((T1 / T0) ** (it / max(1, iterations - 1)))
            p1, p2 = _r.sample(positions, 2)
            current[p1], current[p2] = current[p2], current[p1]
            new_score = self._score_seats(current, rows, cols)
            delta = new_score - current_score
            if delta >= 0 or _r.random() < math.exp(delta / T):
                current_score = new_score
                if new_score > best_score:
                    best_score = new_score
                    best = dict(current)
            else:
                current[p1], current[p2] = current[p2], current[p1]

        return best