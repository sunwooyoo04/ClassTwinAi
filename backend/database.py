import json
from typing import List, Dict, Any, Optional


class Database:
    def __init__(self):
        self._memory = {
            "students": [],
            "relations": [],
            "class_assignments": [],
            "seat_assignments": [],
            "class_documents": [],   # {id, assignment_id, doc_type, document, created_at}
            "seat_documents": [],    # {id, seat_id, doc_type, document, created_at}
        }
        self._id_counter = {
            "students": 1, "relations": 1,
            "class_assignments": 1, "seat_assignments": 1,
            "class_documents": 1, "seat_documents": 1,
        }

    # ==================== 학생 ====================

    def get_all_students(self) -> List[Dict]:
        return self._memory["students"]

    def add_student(self, student: Dict) -> Dict:
        student["id"] = self._id_counter["students"]
        self._id_counter["students"] += 1
        if "ai_traits" not in student:
            student["ai_traits"] = {}  # 기본값 설정
        self._memory["students"].append(student)
        return student

    def update_student(self, student_id: int, data: Dict) -> Dict:
        for s in self._memory["students"]:
            if s["id"] == student_id:
                s.update(data)
                return s
        return {}

    def delete_student(self, student_id: int):
        self._memory["students"] = [s for s in self._memory["students"] if s["id"] != student_id]

    def clear_relations(self):
        """전체 관계 데이터 삭제"""
        self._memory["relations"] = []

    def clear_students(self):
        """전체 학생 삭제 (업로드 리셋용)"""
        self._memory["students"] = []
        self._id_counter["students"] = 1

    def get_students_by_class(self, class_id: int) -> List[Dict]:
        """반배정된 학생 조회"""
        return [s for s in self._memory["students"] if s.get("current_class") == class_id]

    def update_student_traits(self, student_name: str, traits: Dict) -> bool:
        """AI 분석 결과를 학생 데이터에 업데이트"""
        for s in self._memory["students"]:
            if s["name"] == student_name:
                s["ai_traits"] = traits
                return True
        return False

    def get_student_by_name(self, name: str) -> Optional[Dict]:
        """이름으로 학생 조회"""
        for s in self._memory["students"]:
            if s["name"] == name:
                return s
        return None

    # ==================== 관계 ====================

    def get_all_relations(self) -> List[Dict]:
        return self._memory["relations"]

    def add_relation(self, relation: Dict) -> Dict:
        relation["id"] = self._id_counter["relations"]
        self._id_counter["relations"] += 1
        self._memory["relations"].append(relation)
        return relation

    def delete_relation(self, relation_id: int):
        self._memory["relations"] = [r for r in self._memory["relations"] if r["id"] != relation_id]

    # ==================== 반배정 결과 ====================

    def save_class_assignment(self, result: Dict) -> int:
        """반배정 결과 저장"""
        data = {
            "classes": json.dumps(result.get("classes", {})),
            "stability_score": result.get("stability_score", 0),
            "conditions_met": json.dumps(result.get("conditions_met", {})),
            "stability_detail": json.dumps(result.get("stability_detail", {})),
        }
        data["id"] = self._id_counter["class_assignments"]
        self._id_counter["class_assignments"] += 1
        self._memory["class_assignments"].append(data)
        return data["id"]

    def get_class_assignment(self, assignment_id: int) -> Dict:
        for a in self._memory["class_assignments"]:
            if a["id"] == assignment_id:
                result = a.copy()
                if isinstance(result["classes"], str):
                    result["classes"] = json.loads(result["classes"])
                if isinstance(result.get("conditions_met"), str):
                    result["conditions_met"] = json.loads(result["conditions_met"])
                if isinstance(result.get("stability_detail"), str):
                    result["stability_detail"] = json.loads(result["stability_detail"])
                return result
        return {}

    def get_latest_class_assignment(self) -> Optional[Dict]:
        """가장 최근 반배정 결과"""
        if self._memory["class_assignments"]:
            last = self._memory["class_assignments"][-1].copy()
            if isinstance(last["classes"], str):
                last["classes"] = json.loads(last["classes"])
            return last
        return None

    # ==================== 자리배정 결과 ====================

    def save_seat_assignment(self, result: Dict, class_id: Optional[int] = None) -> int:
        data = {
            "id": self._id_counter["seat_assignments"],
            "class_id": class_id,
            "rows": result.get("rows", 0),
            "cols": result.get("cols", 0),
            "seat_grid": json.dumps(result.get("seat_grid", [])),
            "equity_score": result.get("equity_score", 0),
            "conflict_adjacent_pairs": result.get("conflict_adjacent_pairs", 0),
        }
        self._id_counter["seat_assignments"] += 1
        self._memory["seat_assignments"].append(data)
        return data["id"]

    def _seat_type(self, r: int, c: int, rows: int, cols: int) -> str:
        # algorithm._classify_seat과 동일 규칙
        corners = {(0, 0), (0, cols - 1), (rows - 1, 0), (rows - 1, cols - 1)}
        if (r, c) in corners: return "구석"
        if c == 0 or c == cols - 1: return "창가"
        if r == 0: return "앞줄"
        if r == rows - 1: return "뒷줄"
        return "중앙"

    def get_seat_history(self, class_id: int) -> List[Dict]:
        """저장된 자리배정 결과들로부터 학생별 자리유형 누적 카운트를 집계."""
        counts: Dict[str, Dict[str, int]] = {}  # name -> {corner_count, window_count, front_count, ...}
        for row in self._memory["seat_assignments"]:
            if class_id is not None and row.get("class_id") not in (None, class_id):
                continue
            try:
                grid = json.loads(row["seat_grid"])
            except Exception:
                continue
            rows_n = row.get("rows") or len(grid)
            cols_n = row.get("cols") or (len(grid[0]) if grid else 0)
            if not rows_n or not cols_n:
                continue
            for r, gridrow in enumerate(grid):
                for c, cell in enumerate(gridrow):
                    name = (cell or {}).get("name")
                    if not name:
                        continue
                    t = self._seat_type(r, c, rows_n, cols_n)
                    rec = counts.setdefault(name, {"corner_count": 0, "window_count": 0,
                                                    "front_count": 0, "back_count": 0,
                                                    "center_count": 0, "last_seat_type": t})
                    key = {"구석": "corner_count", "창가": "window_count",
                           "앞줄": "front_count", "뒷줄": "back_count",
                           "중앙": "center_count"}[t]
                    rec[key] += 1
                    rec["last_seat_type"] = t
        return [{"student_name": n, "seat_type": rec["last_seat_type"], **rec}
                for n, rec in counts.items()]

    def calculate_equity(self, class_id: int) -> Dict:
        """배정 이력 기반 형평성 지수 — 자리유형이 학생들에게 얼마나 고르게 분배됐는지(0~100)."""
        history = self.get_seat_history(class_id)
        if not history:
            return {"front_row": 0, "window": 0, "corner": 0, "overall": 0, "samples": 0}

        def spread_score(values: List[int]) -> int:
            # 균등할수록 100, 한 사람에게 몰릴수록 낮음
            if not values or sum(values) == 0:
                return 100
            n = len(values)
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n
            # variance가 0이면 100, 클수록 감점
            return max(0, min(100, int(round(100 - variance * 20))))

        front  = spread_score([h["front_count"]  for h in history])
        window = spread_score([h["window_count"] for h in history])
        corner = spread_score([h["corner_count"] for h in history])
        overall = (front + window + corner) // 3
        return {"front_row": front, "window": window, "corner": corner,
                "overall": overall, "samples": len(self._memory["seat_assignments"])}

    # ==================== 생성된 문서 보관 ====================

    def save_class_document(self, assignment_id: int, doc_type: str, document: str) -> int:
        from datetime import datetime, timezone
        row = {
            "id": self._id_counter["class_documents"],
            "assignment_id": assignment_id,
            "doc_type": doc_type,
            "document": document,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._id_counter["class_documents"] += 1
        self._memory["class_documents"].append(row)
        return row["id"]

    def save_seat_document(self, seat_id: Optional[int], doc_type: str, document: str) -> int:
        from datetime import datetime, timezone
        row = {
            "id": self._id_counter["seat_documents"],
            "seat_id": seat_id,
            "doc_type": doc_type,
            "document": document,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._id_counter["seat_documents"] += 1
        self._memory["seat_documents"].append(row)
        return row["id"]

    def get_seat_documents(self, seat_id: int) -> List[Dict]:
        return [d for d in self._memory["seat_documents"] if d["seat_id"] == seat_id]

    def get_class_documents(self, assignment_id: int) -> List[Dict]:
        return [d for d in self._memory["class_documents"] if d["assignment_id"] == assignment_id]


# 싱글톤
db = Database()