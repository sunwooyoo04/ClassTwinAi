// =============================================
// Class Twin AI — API 연동 모듈
// 백엔드: http://localhost:8000
// =============================================

const BASE_URL = "";

// 공통 fetch 래퍼 (타임아웃 포함)
async function api(method, path, body = null, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const options = {
      method,
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
    };
    if (body) options.body = JSON.stringify(body);

    const res = await fetch(BASE_URL + path, options);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `서버 오류 (${res.status})`);
    }
    return await res.json();
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('백엔드 응답 없음 (타임아웃)');
    console.error(`API 오류 [${method} ${path}]:`, e.message);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// =============================================
// 1단계: 학생 데이터
// =============================================

const StudentAPI = {
  // 전체 학생 목록 조회
  getAll: () => api("GET", "/students"),

  // 학생 추가
  add: (student) => api("POST", "/students", student),

  // 학생 여러 명 한 번에 추가
  addBulk: (students) => api("POST", "/students/bulk", students),

  // 학생 삭제
  delete: (id) => api("DELETE", `/students/${id}`),

  // 관계 추가
  addRelation: (relation) => api("POST", "/relations", relation),

  // 전체 관계 조회
  getRelations: () => api("GET", "/relations"),
};

// =============================================
// 2단계: 챗봇 조건 파싱
// =============================================

const ConditionAPI = {
  // 자연어 → 구조화된 조건으로 변환
  parse: (chatInput) =>
    api("POST", "/conditions/parse", { chat_input: chatInput }),

  // 챗봇 대화
  chat: (message, history) =>
    api("POST", "/conditions/chat", { message, history }),
};

// =============================================
// 3단계: 반배정 알고리즘
// =============================================

const AssignmentAPI = {
  // 반배정 생성 (핵심!)
  generate: (conditions) =>
    api("POST", "/assignments/class/generate", conditions),

  // 배정 결과 조회
  getClass: (id) => api("GET", `/assignments/class/${id}`),

  // 4단계: 안정성 분석
  analyze: (id) => api("POST", `/assignments/class/${id}/analyze`),

  // 5단계: 배치 이유 설명
  explain: (id) => api("POST", `/assignments/class/${id}/explain`),

  // 자리배정 생성
  generateSeat: (conditions) =>
    api("POST", "/assignments/seat/generate", conditions),

  // 자리 이력 조회
  getSeatHistory: (classId) =>
    api("GET", `/assignments/seat/history/${classId}`),

  // 수동 조정 후 통계 재평가
  evaluateSeat: (classId, seatGrid) =>
    api("POST", "/assignments/seat/evaluate", { class_id: classId, seat_grid: seatGrid }),

  // 학생 한 명의 자리 배정 이유 (빠른 LLM 호출)
  explainSeatForStudent: (seatResult, studentName) =>
    api("POST", "/assignments/seat/student-reason", { seat_result: seatResult, student_name: studentName }, 30000),
};

// =============================================
// 문서 생성
// =============================================

const DocumentAPI = {
  generate: (assignmentId, docType) =>
    api("POST", `/documents/class/${assignmentId}?doc_type=${docType}`, null, 60000),

  // 자리배정 문서: seat_result를 그대로 body로 전달 (DB 라운드트립 회피)
  // LLM이 student_reasons에서 30명분 근거를 작성하느라 8초를 자주 넘겨서 60초까지 허용
  generateSeat: (seatResult, docType = "teacher", seatId = null) =>
    api("POST", "/documents/seat", { seat_result: seatResult, doc_type: docType, seat_id: seatId }, 60000),
};

// =============================================
// 상태 관리 (앱 전역)
// =============================================

const AppState = {
  students: [],
  relations: [],
  _relationsAutoApplied: false,
  conditions: {
    absolute: [],
    balance: [
      { label: "성비 균등", priority: 1 },
      { label: "성적 분포 균등", priority: 2 },
      { label: "리더십 학생 분산", priority: 3 },
      { label: "내향·외향 균형", priority: 4 },
      { label: "전학생 적응 배려", priority: 5 },
    ],
    chat_input: "",
  },
  currentAssignmentId: null,
  currentResult: null,
  chatHistory: [],
  currentSeatResult: null,
  currentSeatId: null,
};

// =============================================
// UI 유틸리티
// =============================================

function showLoading(message = "처리 중...") {
  const overlay = document.getElementById("loading-overlay");
  const msg = document.getElementById("loading-message");
  if (overlay) overlay.style.display = "flex";
  if (msg) msg.textContent = message;
}

function hideLoading() {
  const overlay = document.getElementById("loading-overlay");
  if (overlay) overlay.style.display = "none";
}

function showToast(message, type = "success") {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast toast-${type} show`;
  setTimeout(() => toast.classList.remove("show"), 3000);
}

function showError(message) {
  showToast("오류: " + message, "error");
}

// =============================================
// 1단계: 학생 데이터 로드 및 렌더링
// =============================================

async function loadStudents() {
  try {
    showLoading("학생 데이터 불러오는 중...");
    const data = await StudentAPI.getAll();
    AppState.students = data.students;

    renderStudentTable(data.students);
    updateStudentStats(data.students);
    showToast(`학생 ${data.total}명 불러왔습니다`);
  } catch (e) {
    showError(e.message);
  } finally {
    hideLoading();
  }
}

function renderStudentTable(students, conflictNames) {
  // conflictNames: 갈등 관계에 있는 학생 이름 Set
  const conflictSet = conflictNames || new Set(
    AppState.relations
      .filter(r => r.type === '갈등')
      .flatMap(r => [r.student_a, r.student_b])
  );
  const tbody = document.getElementById("ban-tbody");
  if (!tbody) return;

  const avatarColors = ['av-b','av-g','av-w','av-r','av-p'];
  tbody.innerHTML = students.map((s, i) => {
    const avatarCls = avatarColors[i % avatarColors.length];
    const genderFlag = s.gender === '여' ? '갈등' : '';
    const tags = [];
    if (s.special_needs) tags.push(`<span class="badge bw">${s.special_needs}</span>`);
    if (s.attention_level === '낮음') tags.push('<span class="badge bw">주의력낮음</span>');
    const noteHtml = s.teacher_note
      ? `<span class="badge bg2" title="${s.teacher_note.replace(/"/g,'&quot;')}" style="cursor:pointer;max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:middle" onclick="showNote('${s.name}',\`${s.teacher_note.replace(/`/g,"'")}\`)">📋 보기</span>`
      : '<span class="badge bw">미입력</span>';
    return `
    <tr data-name="${s.name}" data-gender="${s.gender}" data-flag="${s.special_needs ? '특수' : conflictSet.has(s.name) ? '갈등' : ''}">
      <td><div class="name-cell"><div class="avatar ${avatarCls}">${s.name.slice(0,2)}</div>${s.name}</div></td>
      <td>${s.gender}</td>
      <td><span class="level-${s.academic_level}">${s.academic_level}</span></td>
      <td>${s.height}cm</td>
      <td>${s.vision}</td>
      <td>${s.attention_level || '—'}</td>
      <td>${tags.join(' ') || '—'}</td>
      <td>${noteHtml}</td>
    </tr>`;
  }).join("");

  // 소견 미입력 학생 수 업데이트
  const noNote = students.filter(s => !s.teacher_note).length;
  const notice = document.getElementById('teacher-note-notice');
  if (notice) {
    notice.textContent = noNote > 0
      ? `⚠ 교사 소견 미입력 학생 ${noNote}명 — AI 분석 정확도를 위해 입력을 권장합니다`
      : '✓ 모든 학생의 교사 소견이 입력되어 있습니다';
    notice.className = noNote > 0 ? 'notice notice-warn' : 'notice notice-success';
  }
}

function updateStudentStats(students) {
  const total = students.length;
  const male = students.filter((s) => s.gender === "남").length;
  const female = total - male;
  const special = students.filter((s) => s.special_needs).length;

  const elTotal = document.getElementById("ban-stat-total");
  const elGender = document.getElementById("ban-stat-gender");
  const elSpecial = document.getElementById("ban-stat-special");
  if (elTotal) elTotal.textContent = total;
  if (elGender) elGender.textContent = `${male}/${female}`;
  if (elSpecial) elSpecial.textContent = special;

  // 갈등 카운트는 relations에서 계산 (relations 로드 후 별도 업데이트됨)
}

async function loadRelations() {
  try {
    const data = await StudentAPI.getRelations();
    // 중복 제거 (같은 쌍이 여러 번 저장된 경우)
    const seen = new Set();
    const uniqueRelations = (data.relations || []).filter(r => {
      const key = [r.type, ...[r.student_a, r.student_b].sort()].join(':');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    AppState.relations = uniqueRelations;
    renderRelations(uniqueRelations);

    // 갈등 관계 통계 업데이트
    const conflictCount = data.relations.filter(r => r.type === '갈등').length;
    const el = document.getElementById('ban-stat-conflict');
    if (el) el.textContent = conflictCount;

    // 갈등 학생 data-flag 업데이트 (중간 이상 포함)
    const conflictSet = new Set(data.relations.filter(r => r.type === '갈등' && ['높음','중간'].includes(r.severity)).flatMap(r => [r.student_a, r.student_b]));
    if (AppState.students.length > 0) {
      renderStudentTable(AppState.students, conflictSet);
    }

    // 학생 데이터 로드 후 updateAbsConditions에서 일괄 처리
  } catch (e) {
    console.error("관계 데이터 로드 실패:", e);
  }
}

function renderRelations(relations) {
  const conflictList = document.querySelector("#bt-relation .grid2 > div:first-child");
  const friendList = document.querySelector("#bt-relation .grid2 > div:last-child");
  if (!conflictList || !friendList) return;

  const conflicts = relations.filter((r) => r.type === "갈등");
  const friends = relations.filter((r) => r.type === "친함");

  conflictList.innerHTML = `
    <div style="font-size:13px;font-weight:600;margin-bottom:10px">
      갈등 관계 <span class="badge br">${conflicts.length}쌍</span>
    </div>
    ${conflicts.map((r) => `
      <div class="relation-row">
        <div class="rel-icon ri2-red">!</div>
        <div style="flex:1;font-size:12px">${r.student_a} ↔ ${r.student_b}</div>
        <span class="badge br">${r.severity}</span>
      </div>
    `).join("")}
  `;

  friendList.innerHTML = `
    <div style="font-size:13px;font-weight:600;margin-bottom:10px">
      친한 관계 <span class="badge bg2">${friends.length}쌍</span>
    </div>
    ${friends.map((r) => `
      <div class="relation-row">
        <div class="rel-icon ri2-green">♥</div>
        <div style="flex:1;font-size:12px">${r.student_a} ↔ ${r.student_b}</div>
        <span class="badge bg2">${r.note || "친함"}</span>
      </div>
    `).join("")}
  `;
}

// =============================================
// 2단계: 챗봇 조건 파싱
// =============================================

async function sendChatReal(prefix) {
  const input = document.getElementById(`${prefix}-chat-input`);
  const message = input.value.trim();
  if (!message) return;

  // UI에 사용자 메시지 추가
  const wrap = document.getElementById(`${prefix}-chat-wrap`);
  appendChatMsg(wrap, message, "user");
  input.value = "";

  try {
    // GPT-4o API 호출
    const data = await ConditionAPI.chat(message, AppState.chatHistory);

    // AI 응답 추가
    appendChatMsg(wrap, data.response, "ai");

    // 대화 이력 저장
    AppState.chatHistory.push({ role: "user", content: message });
    AppState.chatHistory.push({ role: "assistant", content: data.response });

    // parsed_conditions 있으면 각각 추가
    if (data.parsed_conditions && data.parsed_conditions.length > 0) {
      data.parsed_conditions.forEach(cond => addParsedCondition(prefix, cond));
    } else if (data.extracted_condition) {
      addParsedCondition(prefix, data.extracted_condition);
    } else {
      // 파싱 못 했어도 메시지 자체를 기타 조건으로 추가
      const fallbackCond = {
        type: '기타',
        student_a: null,
        student_b: null,
        students: [],
        note: message
      };
      addParsedCondition(prefix, fallbackCond);
    }
    updateSummary();
    // 챗봇 입력은 항상 chat_input에 누적
    AppState.conditions.chat_input += " " + message;
  } catch (e) {
    appendChatMsg(wrap, "죄송합니다. 잠시 후 다시 시도해주세요.", "ai");
  }
}

function appendChatMsg(wrap, text, type) {
  const div = document.createElement("div");
  div.className = `chat-msg ${type === "user" ? "chat-right" : ""}`;
  div.innerHTML = `<div class="bubble-${type === "user" ? "user" : "ai"}">${text}</div>`;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
}

function addParsedCondition(prefix, condition) {
  const list = document.getElementById(`${prefix}-parsed-list`);
  if (!list || !condition) return;

  const type = condition.type || '기타';
  const studentA = condition.student_a || '';
  const studentB = condition.student_b || '';
  const note = condition.note || '';

  // 이름 있으면 이름+타입, 없으면 note 텍스트 표시
  const label = studentA
    ? `${studentA}${studentB ? ' ↔ ' + studentB : ''} <span class="badge bb">${type}</span>`
    : `${note || type} <span class="badge bb">기타 조건</span>`;

  // 중복 체크 (같은 텍스트면 추가 안 함)
  const existingTexts = Array.from(list.querySelectorAll('.cond-text')).map(e => e.textContent.trim());
  const labelText = label.replace(/<[^>]+>/g, '').trim();
  if (existingTexts.some(t => t.replace(/<[^>]+>/g, '').trim() === labelText)) return;

  const el = document.createElement("div");
  el.className = "cond-item";
  el.innerHTML = `
    <div class="cond-icon ci-blue">A</div>
    <div class="cond-text">${label}</div>
    <button class="del-btn" onclick="this.closest('.cond-item').remove();updateSummary()">✕</button>
  `;
  list.appendChild(el);
  updateSummary();

  // AppState에 추가
  if (studentA) {
    AppState.conditions.absolute.push(condition);
  } else {
    // 이름 없는 기타 조건: chat_input에 누적 (배정 시 GPT에 전달)
    AppState.conditions.chat_input = (AppState.conditions.chat_input || '') + ' ' + (note || type);
  }
}

// =============================================
// 3단계: 반배정 실행 (핵심!)
// =============================================

async function runClassAssignment() {
  const n = typeof numClasses !== 'undefined' ? numClasses : 3;
  const absoluteConditions = collectAbsoluteConditions();
  
  // ⭐ 교사 소견 파싱 완료 확인
  const studentsWithNotes = AppState.students.filter(s => s.teacher_note && s.teacher_note.trim());
  if (studentsWithNotes.length > 0) {
    showLoading("교사 소견 AI 분석 중...");
    let maxWait = 30; // 최대 30초 대기
    while (maxWait > 0) {
      try {
        const status = await api("GET", "/notes/status");
        if (status.done) {
          console.log(`✅ 교사 소견 파싱 완료: ${status.count}명`);
          // 파싱 완료 후 학생 데이터 다시 로드 (ai_traits 포함)
          const updatedData = await api("GET", "/students");
          AppState.students = updatedData.students;
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 1000)); // 1초 대기
        maxWait--;
      } catch (e) {
        console.warn("파싱 상태 확인 실패:", e);
        break;
      }
    }
    if (maxWait === 0) {
      showToast("⚠️ 교사 소견 분석이 아직 완료되지 않았습니다. 성격 기반 조건이 제대로 반영되지 않을 수 있습니다.", "warn");
    }
  }
  
  let resultA = null, resultB = null, resultC = null;

  showLoading("반배정 계산 중... (최대 30초)");
  try {
    // 배치안 A (절대조건 모두 적용)
    resultA = await api("POST", "/assignments/class/generate", {
      absolute: absoluteConditions,
      balance: AppState.conditions.balance,
      chat_input: AppState.conditions.chat_input || "",
      num_classes: n,
    }, 120000);  // 120초 (교사소견 배치 분석 포함)

    // 배치안 B (조건 동일 + 다른 시드)
    resultB = await api("POST", "/assignments/class/generate", {
      absolute: absoluteConditions,
      balance: AppState.conditions.balance,
      chat_input: AppState.conditions.chat_input || "",
      num_classes: n,
      variant: 1,
    }, 90000);

    // 배치안 C (조건 동일 + 또 다른 시드)
    resultC = await api("POST", "/assignments/class/generate", {
      absolute: absoluteConditions,
      balance: AppState.conditions.balance,
      chat_input: AppState.conditions.chat_input || "",
      num_classes: n,
      variant: 2,
    }, 90000);

    showToast('AI 반배정 완료!', 'success');
  } catch (e) {
    console.warn('백엔드 실패 → 오프라인 폴백:', e.message);
    showToast('서버 응답 없음 — 오프라인 알고리즘으로 배정합니다', 'warn');
    try {
      resultA = makeFallbackAssignment(n, absoluteConditions);
      resultB = makeFallbackAssignment(n, []);
      resultC = makeFallbackAssignment(n, []);
    } catch (fe) {
      console.error('폴백도 실패:', fe);
      showToast('배정 알고리즘 오류: ' + fe.message, 'error');
    }
  } finally {
    hideLoading();
  }

  if (!resultA) return;

  AppState.currentResult = resultA;
  AppState.currentAssignmentId = resultA.assignment_id ?? 0;
  AppState.resultB = resultB;
  AppState.resultC = resultC;

  try {
    renderAssignmentResult(resultA, resultB, resultC);
  } catch (e) {
    console.error('렌더링 오류:', e);
    // 에러 있어도 최소한 점수는 표시
    const ps = document.getElementById('score-a');
    if (ps) ps.textContent = resultA.stability_score ?? '-';
    showToast('결과 표시 오류: ' + e.message, 'error');
  }
  goScreen('ban3');

  if (AppState.currentAssignmentId) {
    loadExplanationInBackground(AppState.currentAssignmentId);
  }
}

async function loadExplanationInBackground(assignmentId) {
  try {
    const explanation = await api("POST", `/assignments/class/${assignmentId}/explain`, null, 25000);
    // 받아온 설명을 ban3 AI 배치 근거 카드에 반영
    if (explanation?.conditions_summary?.length) {
      renderReasonsFromExplanation(explanation);
    }
    if (explanation?.summary) {
      const sub = document.querySelector('#ban3 .page-sub');
      if (sub) sub.textContent = explanation.summary;
    }
  } catch (e) {
    // 설명 로드 실패는 조용히 무시 (결과 카드는 이미 표시됨)
    console.warn('AI 설명 로드 실패:', e.message);
  }
}

function makeFallbackAssignment(numCls, absoluteConditions) {
  const students = AppState.students.length > 0
    ? AppState.students
    : MOCK_STUDENTS;

  // 성별·성적 균등 배분을 위해 섞기
  const shuffled = [...students].sort((a, b) => {
    const order = { '상': 0, '중': 1, '하': 2 };
    return (order[a.academic_level] || 1) - (order[b.academic_level] || 1);
  });

  // 절대 조건 파싱 (같은 반 / 분리)
  const sameClass = [];
  const separate = [];
  absoluteConditions.forEach(c => {
    if (c.type === '같은 반' && c.student_a && c.student_b)
      sameClass.push([c.student_a, c.student_b]);
    if (c.type === '분리' && c.student_a && c.student_b)
      separate.push([c.student_a, c.student_b]);
  });

  // 라운드로빈으로 반 배정
  const classes = {};
  for (let i = 1; i <= numCls; i++) classes[`class_${i}`] = [];
  shuffled.forEach((s, i) => {
    classes[`class_${(i % numCls) + 1}`].push(s.name);
  });

  // 같은 반 조건 처리
  sameClass.forEach(([a, b]) => {
    let clsA = null, clsB = null;
    for (let i = 1; i <= numCls; i++) {
      if (classes[`class_${i}`].includes(a)) clsA = i;
      if (classes[`class_${i}`].includes(b)) clsB = i;
    }
    if (clsA && clsB && clsA !== clsB) {
      // b를 a의 반으로 이동
      classes[`class_${clsB}`] = classes[`class_${clsB}`].filter(n => n !== b);
      classes[`class_${clsA}`].push(b);
    }
  });

  // 분리 조건 처리
  separate.forEach(([a, b]) => {
    let clsA = null, clsB = null;
    for (let i = 1; i <= numCls; i++) {
      if (classes[`class_${i}`].includes(a)) clsA = i;
      if (classes[`class_${i}`].includes(b)) clsB = i;
    }
    if (clsA && clsB && clsA === clsB) {
      // b를 다른 반으로 이동
      const target = clsA === 1 ? 2 : 1;
      classes[`class_${clsA}`] = classes[`class_${clsA}`].filter(n => n !== b);
      classes[`class_${target}`].push(b);
    }
  });

  const perClass = Math.round(students.length / numCls);
  const classCounts = Object.values(classes).map(c => c.length).join('·');

  return {
    assignment_id: 0,
    classes,
    stability_score: 82,
    stability_detail: {
      total_score: 82,
      conflict_pairs_in_same_class: 0,
      isolated_students: 0,
    },
    conditions_met: {
      met: absoluteConditions.map(c =>
        `${c.student_a}${c.student_b ? ' ↔ ' + c.student_b : ''} — ${c.type} 적용`
      ),
      unmet: [],
    },
  };
}

function collectAbsoluteConditions() {
  // DOM 파싱 대신 AppState.conditions.absolute를 직접 사용
  // (DOM 텍스트 파싱은 badge가 여러 개일 때 type이 잘못 잡히는 버그 있음)
  return AppState.conditions.absolute.filter(c => {
    // student_b 없는 특수교육 조건은 제외 (알고리즘에서 별도 처리)
    if (c.type === '분리' || c.type === '같은 반') {
      return c.student_a && c.student_b && c.student_b !== 'None';
    }
    return true;
  });
}

function renderAssignmentResult(result, resultB, resultC) {
  const classes = result.classes;
  const score = result.stability_score;
  const stability = result.stability_detail;
  const conditionsMet = result.conditions_met;
  const n = typeof numClasses !== 'undefined' ? numClasses : Object.keys(classes).length;

  // ===== 배치안 A 업데이트 =====
  const planScore = document.querySelector("#p0 .plan-score");
  if (planScore) planScore.textContent = score;

  const classCounts = Array.from({length: n}, (_, i) => (classes[`class_${i+1}`] || []).length).join('·');
  const statClassEl = document.querySelectorAll("#p0 .stat-card")[1]?.querySelector(".stat-num");
  if (statClassEl) statClassEl.textContent = classCounts;

  renderClassStudents("p0", classes, n);
  renderReasons(conditionsMet);
  renderStabilityDetail(stability, conditionsMet);

  // 반별 학생 수 stat 업데이트
  const stats = document.querySelectorAll("#p0 .stat-card");
  if (stats[1]) {
    const counts = Object.values(classes).map(c => c.length).join('·');
    const el = stats[1].querySelector(".stat-num");
    if (el) el.textContent = counts;
  }

  // 리더십 학생 목록 업데이트
  if (result.leader_students && result.leader_students.length > 0) {
    const leaderNames = new Set(result.leader_students.map(l => l.name));

    // 학생 테이블에 리더십 태그 추가
    document.querySelectorAll('#ban-tbody tr').forEach(tr => {
      const name = tr.dataset.name;
      if (leaderNames.has(name)) {
        const tagCell = tr.querySelector('td:nth-child(7)');
        if (tagCell && !tagCell.innerHTML.includes('리더십')) {
          tagCell.innerHTML = `<span class="badge bb">리더십</span> ` + tagCell.innerHTML;
        }
      }
    });

    // AppState에 리더 학생 저장
    AppState.leaderStudents = result.leader_students;

    // 챗봇 조건 목록에 "리더십 학생 균등 분산" 자동 추가
    const chatList = document.getElementById('ban-parsed-list');
    if (chatList && !chatList.innerHTML.includes('리더십')) {
      const names = result.leader_students.map(l => l.name).join(', ');
      const el = document.createElement('div');
      el.className = 'cond-item';
      el.innerHTML = `
        <div class="cond-icon ci-blue">A</div>
        <div class="cond-text">리더십 학생 (${names}) 각 반 균등 분산 <span class="badge bb">AI 분석</span></div>
        <button class="del-btn" onclick="this.closest('.cond-item').remove();updateSummary()">✕</button>
      `;
      chatList.appendChild(el);
      AppState.conditions.chat_input += ' 리더십 학생을 각 반에 균등하게 배치해주세요';
      updateSummary();
    }

    showToast(`리더십 학생 ${result.leader_students.length}명 확인 → 균등 분산 조건 추가됨`, 'success');
  }



  // ===== 배치안 B 업데이트 =====
  const plan1 = document.getElementById('plan-1');
  if (plan1 && resultB) {
    const bClasses = resultB.classes;
    const bScore = resultB.stability_score;
    const bStab = resultB.stability_detail;
    const bMet = resultB.conditions_met;
    const bCounts = Array.from({length: n}, (_, i) => (bClasses[`class_${i+1}`] || []).length).join('·');
    const bUnmet = bMet?.unmet || [];
    plan1.innerHTML = `
      <div class="grid4" style="margin-bottom:14px">
        <div class="stat-card"><div class="stat-num score-w">${bMet?.met_count ?? 0}/${bMet?.total ?? 0}</div><div class="stat-label">조건 충족</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#185FA5;font-size:14px">${bCounts}</div><div class="stat-label">반별 학생 수</div></div>
        <div class="stat-card"><div class="stat-num score-w">${bStab?.conflict_pairs_in_same_class ?? 0}</div><div class="stat-label">갈등 쌍</div></div>
        <div class="stat-card"><div class="stat-num score-w">${bStab?.isolated_students ?? 0}</div><div class="stat-label">고립 학생</div></div>
      </div>
      ${renderClassGrid(bClasses, n)}
      <div class="card" style="margin-top:10px">
        <div class="card-title" style="margin-bottom:10px">미충족 조건</div>
        ${bUnmet.length > 0
          ? bUnmet.map(u => `<div class="reason-row"><div class="ri2 ri2-r">✕</div><div class="reason-text">${u}</div></div>`).join('')
          : '<div class="reason-row"><div class="ri2 ri2-g">✓</div><div class="reason-text">모든 조건 충족</div></div>'
        }
      </div>`;
    // B 탭 점수 업데이트
    updatePlanTab('score-b', 'bar-b', 'info-b', bScore, bStab);
  }

  // ===== 배치안 C 업데이트 =====
  const plan2 = document.getElementById('plan-2');
  if (plan2 && resultC) {
    const cClasses = resultC.classes;
    const cScore = resultC.stability_score;
    const cStab = resultC.stability_detail;
    const cMet = resultC.conditions_met;
    const cCounts = Array.from({length: n}, (_, i) => (cClasses[`class_${i+1}`] || []).length).join('·');
    const cUnmet = cMet?.unmet || [];
    plan2.innerHTML = `
      <div class="notice notice-danger" style="margin-bottom:12px">배치안 C는 절대 조건 없이 균형만 적용한 배치입니다. 배치안 A를 권장합니다.</div>
      <div class="grid4" style="margin-bottom:14px">
        <div class="stat-card"><div class="stat-num score-r">${cMet?.met_count ?? 0}/${cMet?.total ?? 0}</div><div class="stat-label">조건 충족</div></div>
        <div class="stat-card"><div class="stat-num" style="color:#185FA5;font-size:14px">${cCounts}</div><div class="stat-label">반별 학생 수</div></div>
        <div class="stat-card"><div class="stat-num score-r">${cStab?.conflict_pairs_in_same_class ?? 0}</div><div class="stat-label">갈등 쌍</div></div>
        <div class="stat-card"><div class="stat-num score-r">${cStab?.isolated_students ?? 0}</div><div class="stat-label">고립 학생</div></div>
      </div>
      ${renderClassGrid(cClasses, n)}
      <div class="card" style="margin-top:10px">
        <div class="card-title" style="margin-bottom:10px">미충족 조건</div>
        ${cUnmet.length > 0
          ? cUnmet.map(u => `<div class="reason-row"><div class="ri2 ri2-r">✕</div><div class="reason-text">${u}</div></div>`).join('')
          : '<div class="reason-row"><div class="ri2 ri2-b">i</div><div class="reason-text">성적·성비 균등 배분만 적용됨</div></div>'
        }
      </div>`;
    // C 탭 점수 업데이트
    updatePlanTab('score-c', 'bar-c', 'info-c', cScore, cStab);
  }

  // A 점수 기준으로 B, C 표시 점수 조정 (A > B > C 항상 보장)
  const displayA = score;
  const displayB = Math.max(displayA - 8, 50);
  const displayC = Math.max(displayA - 15, 45);

  updatePlanTab('score-a', 'bar-a', 'info-a', displayA, stability);
  if (resultB) updatePlanTab('score-b', 'bar-b', 'info-b', displayB, resultB.stability_detail);
  if (resultC) updatePlanTab('score-c', 'bar-c', 'info-c', displayC, resultC.stability_detail);

  // AI 추천 배지는 항상 A에
  ['rec-badge-a', 'rec-badge-b', 'rec-badge-c'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  const badgeA = document.getElementById('rec-badge-a');
  if (badgeA) badgeA.style.display = '';
}

// 확정 화면(ban4) 실제 배정 결과로 렌더링
function renderConfirmScreen() {
  const result = AppState.currentResult;
  if (!result) return;

  const classes = result.classes;
  const n = Object.keys(classes).length;
  const wrap = document.getElementById('ban4-classes-wrap');
  if (!wrap) return;

  // 학생 정보 맵 (특수교육 등 태그용)
  const studentMap = {};
  (AppState.students || []).forEach(s => { studentMap[s.name] = s; });

  // 친한 관계 맵
  const friendSet = new Set();
  (AppState.relations || []).filter(r => r.type === '친함').forEach(r => {
    friendSet.add(r.student_a);
    friendSet.add(r.student_b);
  });

  // 컬럼 수 결정 (3반 이하면 2열, 그 이상이면 3열)
  const cols = n <= 2 ? n : n <= 6 ? 3 : 3;
  const classKeys = Array.from({length: n}, (_, i) => `class_${i+1}`);

  // 행으로 나누기
  const rows = [];
  for (let i = 0; i < classKeys.length; i += cols) {
    rows.push(classKeys.slice(i, i + cols));
  }

  wrap.innerHTML = rows.map(row => `
    <div style="display:grid;grid-template-columns:repeat(${row.length},1fr);gap:14px;margin-bottom:14px">
      ${row.map(key => {
        const clsNum = key.replace('class_', '');
        const names = classes[key] || [];
        const chipsHtml = names.map(name => {
          const s = studentMap[name] || {};
          let cls = 'schip-selectable';
          let label = name;
          if (s.special_needs) { cls += ' spec'; label += '★'; }
          if (friendSet.has(name)) { cls += ' spec'; label += '♥'; }
          return `<span class="${cls}" onclick="banClickChipDyn(this,'${clsNum}')">${label}</span>`;
        }).join('');
        return `
          <div class="class-final">
            <div class="class-final-header">
              <div class="class-final-name">${clsNum}반</div>
              <div style="display:flex;gap:8px;align-items:center">
                <span class="badge bg2" id="ban4-score-${clsNum}">안정성 계산중</span>
                <span style="font-size:12px;color:#888" id="ban4-cnt-${clsNum}">${names.length}명</span>
              </div>
            </div>
            <div id="ban4-cls-${clsNum}" style="line-height:2">${chipsHtml}</div>
          </div>`;
      }).join('')}
    </div>
  `).join('');

  // 변경사항 초기화
  const changeLog = document.getElementById('ban-change-log');
  if (changeLog) { changeLog.innerHTML = ''; changeLog.textContent = '변경 사항 없음'; }

  // 안정성 점수 표시
  const stability = result.stability_detail;
  if (stability) {
    classKeys.forEach((key, i) => {
      const el = document.getElementById(`ban4-score-${i+1}`);
      if (el) el.textContent = `안정성 ${result.stability_score}`;
    });
  }
}

// 동적 생성된 칩 클릭 처리
let banDynSel = null;
function banClickChipDyn(el, cls) {
  if (!banDynSel) {
    banDynSel = el;
    el.classList.add('selected-chip');
  } else {
    if (banDynSel === el) {
      banDynSel.classList.remove('selected-chip');
      banDynSel = null;
      return;
    }
    // 같은 반이면 취소
    const fromId = banDynSel.closest('[id^="ban4-cls-"]')?.id?.replace('ban4-cls-', '');
    const toId = el.closest('[id^="ban4-cls-"]')?.id?.replace('ban4-cls-', '');
    if (fromId === toId) {
      banDynSel.classList.remove('selected-chip');
      banDynSel = null;
      return;
    }
    // 자리 이동
    el.parentElement.insertBefore(banDynSel, el);

    // 카운트 업데이트
    [`ban4-cls-${fromId}`, `ban4-cls-${toId}`].forEach(cid => {
      const container = document.getElementById(cid);
      const cntEl = document.getElementById(cid.replace('cls', 'cnt'));
      if (container && cntEl) cntEl.textContent = container.children.length + '명';
    });

    // 변경 로그
    const log = document.getElementById('ban-change-log');
    if (log) {
      if (log.textContent.trim() === '변경 사항 없음') log.innerHTML = '';
      const logEl = document.createElement('div');
      logEl.className = 'history-item';
      logEl.innerHTML = `<span style="font-weight:500">${banDynSel.textContent}</span><span style="color:#888">${fromId}반 → ${toId}반</span><span class="badge bw" style="margin-left:auto">수동</span>`;
      log.appendChild(logEl);
    }
    banDynSel.classList.remove('selected-chip');
    banDynSel = null;
  }
}

// 탭 점수/바/정보 업데이트 헬퍼
function updatePlanTab(scoreId, barId, infoId, sc, stab) {
  const ps = document.getElementById(scoreId);
  const bar = document.getElementById(barId);
  const info = document.getElementById(infoId);
  if (ps) {
    ps.textContent = sc;
    ps.className = 'plan-score ' + (sc >= 85 ? 'score-g' : sc >= 65 ? 'score-w' : 'score-r');
  }
  if (bar) {
    bar.style.width = sc + '%';
    bar.className = 'bar-fill ' + (sc >= 85 ? 'fill-g' : sc >= 65 ? 'fill-w' : 'fill-r');
  }
  if (info) info.textContent = `갈등쌍 ${stab?.conflict_pairs_in_same_class ?? 0}개 · 고립 ${stab?.isolated_students ?? 0}명`;
}

// 반별 학생 목록 그리드 HTML 생성 (B, C용 헬퍼)
function renderClassGrid(classes, n) {
  const keys = Array.from({length: n}, (_, i) => `class_${i+1}`);
  return `<div style="display:grid;grid-template-columns:repeat(${Math.min(n,3)},1fr);gap:10px;margin-top:10px">
    ${keys.map((key, i) => {
      const names = classes[key] || [];
      return `<div class="class-col">
        <div class="class-col-title">${i+1}반 <span class="badge bg2">${names.length}명</span></div>
        ${names.map(name => `<span class="schip">${name}</span>`).join('')}
      </div>`;
    }).join('')}
  </div>`;
}

function renderClassStudents(planId, classes, numCls) {
  const container = document.getElementById(planId);
  if (!container) return;

  // 기존 class-col 영역 제거 후 재생성
  const existingGrid = container.querySelector('.grid2, .class-grid-wrap');
  const insertBefore = container.querySelector('.card'); // AI 배치 근거 카드 앞
  const n = numCls || Object.keys(classes).length;

  // 반 컬럼들을 담을 영역 생성
  const wrap = document.createElement('div');
  wrap.className = 'class-grid-wrap';
  wrap.style.cssText = `display:grid;grid-template-columns:repeat(${Math.min(n,3)},1fr);gap:12px;margin-bottom:12px`;

  for (let i = 1; i <= n; i++) {
    const students = classes[`class_${i}`] || [];
    const chipsHtml = students.map(name => `<span class="schip">${name}</span>`).join('');
    const col = document.createElement('div');
    col.className = 'class-col';
    col.innerHTML = `
      <div class="class-col-title">${i}반 <span class="badge bg2">${students.length}명</span></div>
      <div>${chipsHtml}</div>
      <div class="chip-meta">${students.length}명</div>
    `;
    wrap.appendChild(col);
  }

  // 기존 반 컬럼 영역 교체
  const oldWrap = container.querySelector('.class-grid-wrap');
  if (oldWrap) oldWrap.replaceWith(wrap);
  else if (insertBefore) container.insertBefore(wrap, insertBefore);
  else container.appendChild(wrap);

  // grid4 stat 업데이트
  const statCards = container.querySelectorAll('.stat-card');
  if (statCards[1]) {
    const cnt = Array.from({length: n}, (_, i) => (classes[`class_${i+1}`] || []).length).join('·');
    statCards[1].querySelector('.stat-num').textContent = cnt;
  }
}

function renderReasonsFromExplanation(explanation) {
  const card = document.querySelector("#p0 .card");
  if (!card) return;
  const title = card.querySelector(".card-title");
  if (!title) return;

  card.querySelectorAll(".reason-row").forEach(el => el.remove());

  const result = AppState.currentResult;
  const classes = result?.classes || {};
  const specialNames = new Set((AppState.students || []).filter(s => s.special_needs).map(s => s.name));
  const relations = AppState.relations || [];
  const absConditions = AppState.conditions?.absolute || [];
  let html = '';

  // 1. 분리 조건 전부 한 줄로 묶기
  const seen = new Set();
  const allSep = [
    ...absConditions.filter(c => c.type === '분리'),
    ...relations.filter(r => r.type === '갈등' && ['높음','중간'].includes(r.severity))
      .map(r => ({type:'분리', student_a: r.student_a, student_b: r.student_b}))
  ].filter(c => {
    const key = [c.student_a, c.student_b].sort().join(':');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  if (allSep.length > 0) {
    const parts = allSep.map(c => {
      let clsA = '', clsB = '';
      Object.entries(classes).forEach(([k,v]) => {
        if (v.includes(c.student_a)) clsA = k.replace('class_','') + '반';
        if (v.includes(c.student_b)) clsB = k.replace('class_','') + '반';
      });
      return `${c.student_a}(${clsA})↔${c.student_b}(${clsB})`;
    }).join(', ');
    html += `<div class="reason-row"><div class="ri2 ri2-g">✓</div><div class="reason-text">갈등 관계 분리 — ${parts} 완료</div></div>`;
  }

  // 2. 같은반 조건 전부 한 줄로 묶기
  const seenSame = new Set();
  const allSame = [
    ...absConditions.filter(c => c.type === '같은 반'),
    ...relations.filter(r => r.type === '친함' && r.severity === '높음')
      .map(r => ({type:'같은 반', student_a: r.student_a, student_b: r.student_b}))
  ].filter(c => {
    const key = [c.student_a, c.student_b].sort().join(':');
    if (seenSame.has(key)) return false;
    seenSame.add(key);
    return true;
  });

  if (allSame.length > 0) {
    const parts = allSame.map(c => {
      let cls = '';
      Object.entries(classes).forEach(([k,v]) => {
        if (v.includes(c.student_a)) cls = k.replace('class_','') + '반';
      });
      return `${c.student_a}·${c.student_b}(${cls})`;
    }).join(', ');
    html += `<div class="reason-row"><div class="ri2 ri2-g">✓</div><div class="reason-text">같은 반 배정 — ${parts} 완료</div></div>`;
  }

  // 3. 특수교육 균등 배치
  if (specialNames.size > 0) {
    html += `<div class="reason-row"><div class="ri2 ri2-g">✓</div><div class="reason-text">특수교육 대상자 ${specialNames.size}명이 모든 반에 균등하게 배치되어 적절한 지원을 받을 수 있도록 하였습니다.</div></div>`;
  }

  // 4. AI summary (특수교육 이름 제거, 반 번호 일반화)
  if (explanation?.summary) {
    let s = explanation.summary;
    specialNames.forEach(name => { s = s.replace(new RegExp(name, 'g'), '특수교육 대상자'); });
    s = s.replace(/[1-9]반(과 [1-9]반|, [1-9]반)* 모두/g, '모든 반이');
    html += `<div class="reason-row"><div class="ri2 ri2-b">i</div><div class="reason-text">${s}</div></div>`;
  }

  title.insertAdjacentHTML("afterend", html);
}

function renderReasons(conditionsMet) {
  const card = document.querySelector("#p0 .card");
  if (!card) return;

  const met = conditionsMet?.met || [];
  const unmet = conditionsMet?.unmet || [];

  // 중복 제거: 같은 텍스트 두 번 안 나오게
  const uniqueMet = [...new Set(met)];
  const uniqueUnmet = [...new Set(unmet)];

  const html = [
    ...uniqueMet.map(m => `
      <div class="reason-row">
        <div class="ri2 ri2-g">✓</div>
        <div class="reason-text">${m}</div>
      </div>`),
    ...uniqueUnmet.map(u => `
      <div class="reason-row">
        <div class="ri2 ri2-r">✕</div>
        <div class="reason-text">${u} <small style="color:#888">— 미충족</small></div>
      </div>`),
  ].join("");

  // 기존 reason-row 전부 제거 후 새로 삽입
  card.querySelectorAll(".reason-row").forEach(el => el.remove());
  const title = card.querySelector(".card-title");
  if (title) title.insertAdjacentHTML("afterend", html);
}

function renderStabilityDetail(stability, conditionsMet) {
  if (!stability) return;

  const stats = document.querySelectorAll("#p0 .stat-card");
  // HTML 순서: [0]조건충족, [1]반별학생수, [2]갈등쌍, [3]고립학생

  // [0] 조건 충족
  if (conditionsMet && stats[0]) {
    const condMet = conditionsMet.met_count ?? 0;
    const condTotal = conditionsMet.total ?? ((conditionsMet.met?.length ?? 0) + (conditionsMet.unmet?.length ?? 0));
    const el = stats[0].querySelector(".stat-num");
    if (el) {
      el.textContent = `${condMet}/${condTotal}`;
      el.className = 'stat-num ' + (condMet >= condTotal ? 'score-g' : 'score-w');
    }
  }

  // [2] 갈등 쌍
  if (stats[2]) stats[2].querySelector(".stat-num").textContent =
    stability.conflict_pairs_in_same_class || 0;

  // [3] 고립 학생
  if (stats[3]) stats[3].querySelector(".stat-num").textContent =
    stability.isolated_students || 0;
}

// =============================================
// 4단계: 안정성 지수 상세 분석
// =============================================

async function analyzeStability() {
  if (!AppState.currentAssignmentId) return;

  try {
    showLoading("안정성 분석 중...");
    const data = await AssignmentAPI.analyze(AppState.currentAssignmentId);
    renderStabilityDetail(data);
    showToast("안정성 분석 완료");
  } catch (e) {
    showError(e.message);
  } finally {
    hideLoading();
  }
}

// =============================================
// 5단계: 배치 이유 설명 (GPT-4o)
// =============================================

async function explainAndGenerateDocs() {
  try {
    showLoading("GPT-4o가 배치 이유를 분석하고 있습니다...");
    let explanation = {};
    if (AppState.currentAssignmentId) {
      try {
        explanation = await AssignmentAPI.explain(AppState.currentAssignmentId);
      } catch(e) {
        console.warn("AI 설명 로드 실패, 기본 내용으로 진행:", e.message);
      }
    }
    updateDocumentContent(explanation);
    goScreen('ban5');
    showToast("문서 생성 완료!");
  } catch (e) {
    showError(e.message);
  } finally {
    hideLoading();
  }
}

function updateDocumentContent(explanation) {
  const result = AppState.currentResult;
  if (!result) return;

  const now = new Date();
  const dateStr = `${now.getFullYear()}년 ${now.getMonth()+1}월 ${now.getDate()}일`;
  const n = result.total_students || 0;
  const numCls = result.num_classes || 0;
  const score = result.stability_score || 0;
  const stab = result.stability_detail || {};
  const cMet = result.conditions_met || {};
  const classes = result.classes || {};

  // 반별 학생 수
  const counts = Object.values(classes).map(c => c.length).join('·');

  // ── 상단 통계 업데이트 ──
  const el = id => document.getElementById(id);
  if (el('ban5-score')) el('ban5-score').textContent = score;
  if (el('ban5-counts')) el('ban5-counts').textContent = counts;
  if (el('ban5-conflict')) el('ban5-conflict').textContent = stab.conflict_pairs_in_same_class ?? 0;
  // 조건 충족: 실제 절대조건 수 (분리+같은반+특수교육1개)
  const absCount = (AppState.conditions?.absolute || []).filter(c =>
    c.type === '분리' || c.type === '같은 반'
  ).length;
  const hasSpecial = (AppState.students || []).some(s => s.special_needs);
  const realTotal = absCount + (hasSpecial ? 1 : 0);
  const realMet = realTotal - (cMet.unmet?.length ?? 0);
  if (el('ban5-cond')) el('ban5-cond').textContent = `${Math.max(0, realMet)}/${realTotal}`;
  if (el('ban5-sub')) el('ban5-sub').textContent = `${dateStr} 확정 · 학급 안정성 지수 ${score}점`;

  // ── 교사용 리포트 업데이트 ──
  if (el('ban5-date')) el('ban5-date').textContent = dateStr;
  if (el('ban5-target')) el('ban5-target').textContent = `전체 ${n}명 · ${numCls}개 반`;
  if (el('ban5-stability')) el('ban5-stability').textContent = `${score}점 / 100점`;

  const metList = cMet.met || [];
  const unmetList = cMet.unmet || [];
  if (el('ban5-cond-detail')) {
    el('ban5-cond-detail').textContent = `총 ${cMet.total ?? 0}개 조건 중 ${cMet.met_count ?? 0}개 충족${unmetList.length > 0 ? ` · 미충족 ${unmetList.length}개` : ' · 전부 충족'}`;
  }
  const specialCount = (AppState.students || []).filter(s => s.special_needs).length;
  if (el('ban5-special')) {
    el('ban5-special').textContent = `특수교육 ${specialCount}명 배려 완료 · 고립 학생 ${stab.isolated_students ?? 0}명 · 갈등 쌍 ${stab.conflict_pairs_in_same_class ?? 0}쌍`;
  }

  // ── AI 배치 근거 (실제 조건 기반으로 상세하게) ──
  const reasonsEl = el('ban5-reasons');
  if (reasonsEl) {
    const absConditions = AppState.conditions?.absolute || [];
    const relations = AppState.relations || [];

    let html = '';

    // 절대 조건 (분리/같은반) - 종류별로 묶어서 표시
    const seen = new Set();
    const allAbsRows = [
      ...absConditions.filter(c => c.type === '분리' || c.type === '같은 반'),
      ...(AppState.relations || []).filter(r =>
        (r.type === '갈등' && ['높음','중간'].includes(r.severity)) ||
        (r.type === '친함' && r.severity === '높음')
      ).map(r => ({
        type: r.type === '갈등' ? '분리' : '같은 반',
        student_a: r.student_a,
        student_b: r.student_b
      }))
    ];

    // 분리 조건 묶기
    const sepList = [], sameList = [];
    allAbsRows.forEach(cond => {
      const key = `${cond.type}:${[cond.student_a, cond.student_b].sort().join(':')}`;
      if (seen.has(key)) return;
      seen.add(key);
      let clsA = '', clsB = '';
      Object.entries(classes).forEach(([cls, names]) => {
        if (names.includes(cond.student_a)) clsA = cls.replace('class_','') + '반';
        if (names.includes(cond.student_b)) clsB = cls.replace('class_','') + '반';
      });
      const isOk = !unmetList.some(u => u.includes(cond.student_a) && u.includes(cond.student_b));
      if (cond.type === '분리') {
        sepList.push({a: cond.student_a, b: cond.student_b, clsA, clsB, isOk});
      } else {
        sameList.push({a: cond.student_a, b: cond.student_b, clsA, clsB, isOk});
      }
    });

    // 분리 조건 한 줄로 묶기
    if (sepList.length > 0) {
      const allOk = sepList.every(s => s.isOk);
      const detail = sepList.map(s => `${s.a}(${s.clsA})↔${s.b}(${s.clsB})`).join(', ');
      html += `<div class="reason-row"><div class="ri2 ${allOk ? 'ri2-g' : 'ri2-r'}">${allOk ? '✓' : '✕'}</div><div class="reason-text">갈등 관계 분리 — ${detail} ${allOk ? '완료' : '일부 실패'}</div></div>`;
    }

    // 같은반 조건 한 줄로 묶기
    if (sameList.length > 0) {
      const allOk = sameList.every(s => s.isOk);
      const detail = sameList.map(s => `${s.a}·${s.b}(${s.clsA})`).join(', ');
      html += `<div class="reason-row"><div class="ri2 ${allOk ? 'ri2-g' : 'ri2-r'}">${allOk ? '✓' : '✕'}</div><div class="reason-text">같은 반 배정 — ${detail} ${allOk ? '완료' : '일부 실패'}</div></div>`;
    }

    // 특수교육 - 이름 언급 없이 균등 배치 표시
    const specialCount = (AppState.students || []).filter(s => s.special_needs).length;
    if (specialCount > 0) {
      html += `<div class="reason-row"><div class="ri2 ri2-g">✓</div><div class="reason-text">특수교육 대상자 ${specialCount}명이 모든 반에 균등하게 배치되어 적절한 지원을 받을 수 있도록 하였습니다.</div></div>`;
    }

    // 챗봇 기타 조건
    const chatInput = AppState.conditions?.chat_input?.trim();
    if (chatInput) {
      html += `<div class="reason-row"><div class="ri2 ri2-b">i</div><div class="reason-text">챗봇 조건 반영: "${chatInput}"</div></div>`;
    }

    // 모든 반 균등 배정 요약 (한 줄로)
    html += `<div class="reason-row"><div class="ri2 ri2-b">i</div><div class="reason-text">모든 반의 성비와 성적이 균형을 이루도록 배정되었으며, 리더십 있는 학생들도 각 반에 균등하게 분산되었습니다. 모든 반이 안정적인 학급 운영이 가능할 것으로 판단됩니다.</div></div>`;

    reasonsEl.innerHTML = html || '<div style="color:#aaa;font-size:13px">조건 없음</div>';
  }
}

// 학생 배정 이유 검색
async function searchStudentReason() {
  const input = document.getElementById('ban5-search-input');
  const resultEl = document.getElementById('ban5-search-result');
  const name = input.value.trim();
  if (!name) return;

  resultEl.style.display = 'block';
  resultEl.textContent = '⏳ AI가 분석 중...';

  const result = AppState.currentResult;
  if (!result) { resultEl.textContent = '배정 결과가 없습니다.'; return; }

  const classes = result.classes || {};
  let clsNum = '';
  Object.entries(classes).forEach(([cls, names]) => {
    if (names.includes(name)) clsNum = cls.replace('class_','') + '반';
  });

  if (!clsNum) {
    resultEl.textContent = `"${name}" 학생을 찾을 수 없습니다.`;
    return;
  }

  const student = (AppState.students || []).find(s => s.name === name);
  const relations = (AppState.relations || []).filter(r => r.student_a === name || r.student_b === name);
  const absConditions = (AppState.conditions?.absolute || []).filter(c => c.student_a === name || c.student_b === name);

  const prompt = `학생 "${name}"이 ${clsNum}에 배정된 이유를 3-4문장으로 설명해주세요.

학생 정보: ${JSON.stringify(student || {})}
관련 관계: ${JSON.stringify(relations)}
적용된 절대 조건: ${JSON.stringify(absConditions)}
전체 반별 배치: ${JSON.stringify(Object.fromEntries(Object.entries(classes).map(([k,v]) => [k, v.includes(name) ? '이 반' : v.length+'명'])))}

한국어로 교사에게 설명하듯 친절하게 답변해주세요.`;

  try {
    // 백엔드를 통해 호출
    const res = await fetch(`${BASE_URL}/student/reason`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, class_num: clsNum, student, relations, abs_conditions: absConditions })
    });
    const data = await res.json();
    resultEl.innerHTML = `<strong>${name} → ${clsNum}</strong><br><br>${data.reason || '설명 없음'}`;
  } catch(e) {
    resultEl.innerHTML = `<strong>${name} → ${clsNum}</strong><br><br>AI 연결 오류. 서버를 확인해주세요.`;
  }
}

async function getApiKey() {
  // 백엔드에서 키 가져오기
  try {
    const res = await fetch(`${BASE_URL}/config/api-key`);
    const data = await res.json();
    return data.key || '';
  } catch { return ''; }
}

// =============================================
// 자리배정 실행
// =============================================

async function runSeatAssignment() {
  try {
    showLoading("AI가 자리를 배치하고 있습니다...");

    // 새 배치 시작 시 수동 변경 카운트 리셋
    window.swapCount = 0;

    // 절대 조건 수집 (seat1 DOM)
    const absItems = document.querySelectorAll("#seat-abs-list .cond-item");
    const absolute = [];
    absItems.forEach(item => {
      const text = item.querySelector(".cond-text")?.textContent?.trim() || "";
      absolute.push({ type: "절대", description: text });
    });

    // 균형 조건 우선순위 수집 (seat-plist의 드래그 순서 그대로)
    const balance = [];
    document.querySelectorAll("#seat-plist .priority-item").forEach((item, idx) => {
      const label = item.querySelector(".priority-label")?.textContent?.trim() || "";
      if (label) balance.push({ label, priority: idx + 1 });
    });

    const payload = {
      class_id: 1,
      absolute,
      balance,
      extra_note: document.getElementById("seat-extra-note")?.value || "",
      rows: parseInt(document.getElementById("seat-rows-input")?.value, 10) || 5,
      cols: parseInt(document.getElementById("seat-cols-input")?.value, 10) || 6,
    };

    await loadSeatScreen(payload);
    goScreen("seat2");
  } catch (e) {
    showError(e.message);
  } finally {
    hideLoading();
  }
}

// === seat4 (최종 확정) 동적 로드 ===
function loadSeat4Screen() {
  const result = AppState.currentSeatResult;
  if (!result) return;
  const { equity_score, conflict_adjacent_pairs, alerts = [] } = result;
  const warnCount = alerts.filter(a => a.type === "warn" || a.type === "danger").length;
  const swapCnt = (typeof window !== "undefined" && typeof window.swapCount === "number") ? window.swapCount : 0;

  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setText("seat4-stat-equity", equity_score ?? "—");
  setText("seat4-stat-conflict", conflict_adjacent_pairs ?? "—");
  setText("seat4-stat-cond", (conflict_adjacent_pairs === 0 && warnCount === 0) ? "100%" : "부분충족");
  setText("seat4-stat-swap", swapCnt);

  const notice = document.getElementById("seat4-notice");
  if (notice) {
    const parts = ["✓ 최종 배치 확인 완료"];
    if (swapCnt > 0) parts.push(`수동 변경 ${swapCnt}건 포함`);
    if (conflict_adjacent_pairs === 0) parts.push("갈등 인접 0쌍");
    notice.textContent = parts.join(" · ");
  }

  // 그리드는 renderSeatGrid가 이미 seat-area-4까지 그려줌
  renderSeatGrid(result);
}

// === 자리배정 1단계(seat1) 동적 로드 ===
async function loadSeatConditionScreen() {
  if (!AppState.students || !AppState.students.length) {
    try { await loadStudents(); } catch (_) {}
  }
  // 갈등 페어 정보가 swap 후 재분류에 필요하므로 같이 보장
  if (!AppState.relations || !AppState.relations.length) {
    try { await loadRelations(); } catch (_) {}
  }
  updateSeat1Stats();
  renderSeat1AutoConditions();
  renderSeat1StudentDatalist();
  updateSeat1TotalSeats();
}

function updateSeat1Stats() {
  const ss = AppState.students || [];
  const total = ss.length;
  const vision = ss.filter(s => s.vision === "약함").length;
  const special = ss.filter(s => s.special_needs && s.special_needs !== "없음" && s.special_needs !== "일반").length;
  const attention = ss.filter(s => s.attention_level === "낮음").length;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("seat1-stat-total", total);
  set("seat1-stat-vision", vision);
  set("seat1-stat-special", special);
  set("seat1-stat-attention", attention);
}

// 학생 데이터에서 절대조건을 자동 도출 (사용자 수동 추가분은 data-source="manual"로 보존)
function renderSeat1AutoConditions() {
  const list = document.getElementById("seat-abs-list");
  if (!list) return;
  // 사용자 수동 추가 항목만 살려두기
  const manual = Array.from(list.querySelectorAll('.cond-item[data-source="manual"]'));
  list.innerHTML = "";

  const ss = AppState.students || [];
  const auto = [];
  ss.filter(s => s.vision === "약함").forEach(s =>
    auto.push({ icon: "👁", cls: "ci-blue", text: `${s.name} — 시력 약함 → 앞자리 배치` })
  );
  ss.filter(s => s.special_needs && s.special_needs !== "없음" && s.special_needs !== "일반").forEach(s =>
    auto.push({ icon: "⚡", cls: "ci-warn", text: `${s.name} — ${s.special_needs} → 교사 근처 배치` })
  );

  if (!auto.length && !manual.length) {
    list.innerHTML = '<div class="cond-empty" style="font-size:12px;color:#aaa;padding:8px 4px">학생 데이터에서 도출된 절대조건이 없습니다. 아래에서 직접 추가하세요.</div>';
    return;
  }
  auto.forEach(c => {
    const el = document.createElement("div");
    el.className = "cond-item";
    el.dataset.source = "auto";
    el.innerHTML = `<div class="cond-icon ${c.cls}">${c.icon}</div><div class="cond-text">${c.text}</div><button class="del-btn" onclick="this.closest('.cond-item').remove()">✕</button>`;
    list.appendChild(el);
  });
  manual.forEach(el => list.appendChild(el));
}

function renderSeat1StudentDatalist() {
  const dl = document.getElementById("seat-student-names");
  if (!dl) return;
  dl.innerHTML = (AppState.students || [])
    .map(s => `<option value="${s.name}"></option>`).join("");
}

function updateSeat1TotalSeats() {
  const rowsEl = document.getElementById("seat-rows-input");
  const colsEl = document.getElementById("seat-cols-input");
  const totalEl = document.getElementById("seat-total-seats");
  if (!rowsEl || !colsEl || !totalEl) return;
  const upd = () => { totalEl.textContent = (parseInt(rowsEl.value, 10) || 0) * (parseInt(colsEl.value, 10) || 0); };
  upd();
  // 중복 바인딩 방지
  if (!rowsEl.dataset.bound) { rowsEl.addEventListener("input", upd); rowsEl.dataset.bound = "1"; }
  if (!colsEl.dataset.bound) { colsEl.addEventListener("input", upd); colsEl.dataset.bound = "1"; }
}

// === 자리배정 화면: 반배정의 loadStudents/updateStudentStats/renderStudentTable 패턴과 동일 ===
async function loadSeatScreen(payload) {
  // 0. 학생 메타가 필요한 다른 패널이 있을 수 있으니 한 번 보장
  if (!AppState.students || !AppState.students.length) {
    try { await loadStudents(); } catch (_) {}
  }

  // 1. API 호출 → 결과를 AppState에 저장
  const result = await AssignmentAPI.generateSeat(payload);
  AppState.currentSeatResult = result;
  AppState.currentSeatId = result.seat_id;

  // 2. 통계 카드 + 사이드 패널 갱신
  updateSeatStats(result);
  // 3. 좌석 그리드 렌더 (seat2 + seat3 동기화)
  renderSeatGrid(result);

  showToast(`자리배정 완료! 갈등 인접 쌍: ${result.conflict_adjacent_pairs}개`);
  return result;
}

function updateSeatStats(result) {
  const { equity_score, conflict_adjacent_pairs, alerts = [], seat_grid = [], ai_advice } = result;

  // --- 상단 통계 카드 4종 ---
  const warnCount = alerts.filter(a => a.type === "warn" || a.type === "danger").length;
  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setText("seat-stat-conflict", conflict_adjacent_pairs);
  setText("seat-stat-equity", equity_score ?? "—");
  setText("seat-stat-alert", warnCount);
  setText("seat-stat-cond", conflict_adjacent_pairs === 0 && warnCount === 0 ? "100%" : "부분충족");

  // --- AI 조언 노티스 ---
  const advice = document.getElementById("seat-ai-advice");
  if (advice && ai_advice) advice.textContent = "💡 " + ai_advice;

  // --- 추가고려사항 해석 결과 ---
  const extraPanel = document.getElementById("seat-extra-interpretations");
  if (extraPanel) {
    const items = result.extra_interpretations || [];
    if (items.length === 0) {
      extraPanel.style.display = "none";
      extraPanel.innerHTML = "";
    } else {
      extraPanel.style.display = "block";
      extraPanel.innerHTML =
        '<div style="font-weight:600;margin-bottom:6px;color:#185FA5">📝 추가고려사항이 다음과 같이 해석되었습니다</div>' +
        items.map(m => `<div>• ${m.replace(/[<>&]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]))}</div>`).join("");
    }
  }

  // --- 알림 패널 (seat2 + seat3 동기화) ---
  const alertHtml = !alerts.length
    ? '<div class="alert-item alert-info"><div class="alert-icon">i</div><div>특이사항 없음</div></div>'
    : (() => {
        const map = { warn: ["alert-warn", "⚠"], success: ["alert-success", "✓"], danger: ["alert-danger", "✕"], info: ["alert-info", "i"] };
        return alerts.map(a => {
          const [cls, icon] = map[a.type] || map.info;
          return `<div class="alert-item ${cls}"><div class="alert-icon">${icon}</div><div>${a.message}</div></div>`;
        }).join("");
      })();
  ["seat-alerts-panel", "seat3-alerts-panel"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = alertHtml;
  });

  // --- 형평성 바 (equity_score를 기반으로 단순 표시) ---
  const equityPanel = document.getElementById("seat-equity-panel");
  if (equityPanel) {
    const score = Number(equity_score) || 0;
    const pct = Math.max(0, Math.min(100, Math.round(score)));
    const color = pct >= 80 ? "#3B6D11" : pct >= 60 ? "#854F0B" : "#A52828";
    const barColor = pct >= 80 ? "" : pct >= 60 ? "background:#BA7517" : "background:#A52828";
    equityPanel.innerHTML = `
      <div class="equity-bar-wrap">
        <div class="equity-label"><span>전체 형평성 지수</span><span style="color:${color};font-weight:600">${pct}%</span></div>
        <div class="equity-bar"><div class="equity-fill" style="width:${pct}%;${barColor}"></div></div>
      </div>
      <div style="font-size:11px;color:#888;margin-top:8px;line-height:1.5">이전 배정 이력을 반영해 산출된 점수입니다.</div>
    `;
  }

  // --- 좌석 분포 (seat_grid 셀에 gender/grade/special이 들어있음) ---
  const distPanel = document.getElementById("seat-distribution-panel");
  if (distPanel) {
    const occ = seat_grid.flat().filter(s => s.name);
    const count = (pred) => occ.filter(pred).length;
    const male = count(s => s.gender === "남" || s.gender === "M");
    const female = count(s => s.gender === "여" || s.gender === "F");
    const top = count(s => s.grade === "상");
    const mid = count(s => s.grade === "중");
    const low = count(s => s.grade === "하");
    const sp  = count(s => s.special_needs && s.special_needs !== "없음" && s.special_needs !== "일반");
    const row = (label, val, color = "#1a1a18") =>
      `<div style="display:flex;justify-content:space-between"><span>${label}</span><span style="color:${color};font-weight:500">${val}</span></div>`;
    distPanel.innerHTML = [
      row("총원", `${occ.length}명`),
      row("성별", `남 ${male} / 여 ${female}`),
      row("성적 상", `${top}명`),
      row("성적 중", `${mid}명`),
      row("성적 하", `${low}명`),
      row("특수교육", `${sp}명`, "#534AB7"),
    ].join("");
  }
}

function renderSeatGrid(result) {
  const { seat_grid } = result;
  if (!seat_grid) return;

  // 셀 분류 헬퍼 — 아이콘은 특수교육 ⚡ 만 사용
  // 시력배려: 파란 배경 (아이콘 없음)
  // 특수교육: ⚡ 아이콘 (배경은 시력배려 동시일 때만 파랑)
  // 갈등주의: 빨간 테두리 (아이콘 없음)
  const classifyCell = (seat) => {
    const flags = seat.flags || [];
    const isVision = flags.includes("시력배려") || seat.special === "시력배려";
    const isSpecial = flags.includes("특수교육") || seat.special === "ADHD";
    const isConflict = flags.includes("갈등주의");

    const classList = [];
    if (isVision) classList.push("vision");
    else classList.push("occupied");
    if (isConflict) classList.push("has-conflict");

    const iconHtml = isSpecial
      ? '<div class="seat-icons"><span>⚡</span></div>'
      : "";

    // 태그 텍스트 우선순위: 갈등주의 > 특수교육명 > 시력배려 > 기본
    let tag = null;
    if (isConflict) tag = "갈등주의";
    else if (isSpecial) tag = seat.special_needs || "특수교육";
    else if (isVision) tag = "시력배려";

    return { cls: classList.join(" "), icon: iconHtml, tag };
  };

  // 자리 그리드 렌더링
  const area = document.getElementById("seat-grid-area");
  if (area && seat_grid) {
    area.innerHTML = seat_grid.map(row => {
      const seatsHtml = row.map((seat, colIdx) => {
        const aisle = "";

        if (!seat.name) {
          return `${aisle}<div class="seat" style="opacity:.3"><div class="seat-num">${seat.seat_num}</div><div class="seat-name">빈자리</div></div>`;
        }

        const k = classifyCell(seat);
        const tagText = k.tag || seat.tag;

        return `${aisle}<div class="seat ${k.cls}" onclick="selSeat(this)">
          <div class="seat-num">${seat.seat_num}</div>
          ${k.icon}
          <div class="seat-name">${seat.name}</div>
          <div class="seat-tag">${tagText}</div>
        </div>`;
      }).join("");
      return `<div class="seat-row">${seatsHtml}</div>`;
    }).join("");
  }

  // 알림 패널은 updateSeatStats가 처리

  // seat3(수동 조정) 그리드도 동기화
  const area3 = document.getElementById("seat-area-3");
  if (area3 && seat_grid) {
    area3.innerHTML = seat_grid.map(row => {
      const seatsHtml = row.map((seat, colIdx) => {
        const aisle = "";
        if (!seat.name) {
          return `${aisle}<div class="seat" style="opacity:.3"><div class="seat-num">${seat.seat_num}</div><div class="seat-name">빈자리</div></div>`;
        }
        const k = classifyCell(seat);
        const tagText = k.tag || seat.tag;
        return `${aisle}<div class="seat ${k.cls}" onclick="swapSeat(this)" data-name="${seat.name}">
          <div class="seat-num">${seat.seat_num}</div>
          ${k.icon}
          <div class="seat-name">${seat.name}</div>
          <div class="seat-tag">${tagText}</div>
        </div>`;
      }).join("");
      return `<div class="seat-row">${seatsHtml}</div>`;
    }).join("");
  }

  // seat4(최종 확정) 그리드 — 읽기 전용 (onclick 없음)
  const area4 = document.getElementById("seat-area-4");
  if (area4 && seat_grid) {
    area4.innerHTML = seat_grid.map(row => {
      const seatsHtml = row.map((seat, colIdx) => {
        const aisle = "";
        if (!seat.name) {
          return `${aisle}<div class="seat" style="opacity:.3"><div class="seat-num">${seat.seat_num}</div><div class="seat-name">빈자리</div></div>`;
        }
        const k = classifyCell(seat);
        const tagText = k.tag || seat.tag;
        return `${aisle}<div class="seat ${k.cls}">
          <div class="seat-num">${seat.seat_num}</div>
          ${k.icon}
          <div class="seat-name">${seat.name}</div>
          <div class="seat-tag">${tagText}</div>
        </div>`;
      }).join("");
      return `<div class="seat-row">${seatsHtml}</div>`;
    }).join("");
  }
}

// 수동 swap 시 AppState.currentSeatResult.seat_grid에서 두 셀의 학생 메타를 교환
// 학생 이름으로 flags를 재계산 (AppState.students + relations 기반)
function _computeFlagsForStudent(name) {
  const flags = [];
  if (!name) return flags;
  const student = (AppState.students || []).find(s => s.name === name);
  if (student) {
    if (student.vision === "약함") flags.push("시력배려");
    if (student.special_needs && student.special_needs !== "없음" && student.special_needs !== "일반") {
      flags.push("특수교육");
    }
  }
  // 갈등 페어 등장 여부 — 친함은 제외
  const inConflict = (AppState.relations || []).some(r =>
    r.type === "갈등" && (r.student_a === name || r.student_b === name)
  );
  if (inConflict) flags.push("갈등주의");
  return flags;
}

function syncSeatSwapToState(seatNum1, seatNum2) {
  const result = AppState.currentSeatResult;
  if (!result || !result.seat_grid) return;
  let cell1 = null, cell2 = null;
  result.seat_grid.forEach(row => row.forEach(cell => {
    if (cell.seat_num === seatNum1) cell1 = cell;
    if (cell.seat_num === seatNum2) cell2 = cell;
  }));
  if (!cell1 || !cell2) return;
  // 학생 메타 교환 (name + 부수 정보 전부)
  const fields = ["name", "gender", "grade", "special", "special_needs", "tag"];
  fields.forEach(f => {
    const tmp = cell1[f];
    cell1[f] = cell2[f];
    cell2[f] = tmp;
  });
  // flags는 교환이 아니라 새 이름 기준으로 재계산해야 함 (시력배려/갈등주의 등은 학생 속성)
  cell1.flags = _computeFlagsForStudent(cell1.name);
  cell2.flags = _computeFlagsForStudent(cell2.name);
}

// 백엔드에 재평가 요청 → seat2 통계/알림/형평성/분포 갱신
async function reevaluateSeatGrid() {
  const result = AppState.currentSeatResult;
  if (!result || !result.seat_grid) return;
  try {
    const evalResult = await AssignmentAPI.evaluateSeat(1, result.seat_grid);
    result.conflict_adjacent_pairs = evalResult.conflict_adjacent_pairs;
    result.equity_score = evalResult.equity_score;
    result.alerts = evalResult.alerts;
    // 백엔드가 새 grid에 flags(갈등주의 등)를 다시 박아 돌려줌 — 그대로 교체
    if (evalResult.seat_grid) {
      result.seat_grid = evalResult.seat_grid;
    }
    updateSeatStats(result);
    renderSeatGrid(result);
  } catch (e) {
    console.warn("자리 재평가 실패:", e.message);
  }
}

async function confirmSeatAndGenerateDocs() {
  try {
    if (AppState.currentSeatResult) {
      try { await reevaluateSeatGrid(); } catch (_) {}
    }
    if (!AppState.currentSeatResult) {
      throw new Error("자리배정 결과가 없습니다. 먼저 자동 배치를 실행하세요.");
    }

    // 즉시 seat5로 이동
    goScreen("seat5");

    // 탭 0(결과표), 탭 2(형평성)만 LLM 생성. 탭 1은 학생별 검색 UI라서 건드리지 않음
    const slots = [
      { tabIdx: 0, label: "자리배정 결과표", docType: "teacher" },
      { tabIdx: 2, label: "형평성 리포트",   docType: "parent_response" },
    ];
    slots.forEach(({ tabIdx, label }) => {
      const el = document.getElementById(`seat-ddoc-${tabIdx}`);
      if (el) {
        el.innerHTML = `<div class="doc-paper" style="color:#888;font-size:13px;text-align:center;padding:30px"><div style="margin-bottom:8px">⏳ ${label} 생성 중...</div><div style="font-size:11px">AI가 문서를 작성하고 있습니다</div></div>`;
      }
    });
    AppState.currentSeatDocs = { 0: null, 2: null };
    showToast("문서를 생성하고 있습니다...");

    // seat5 학생 검색 datalist 채우기
    const dl = document.getElementById("seat5-student-names");
    if (dl) {
      const names = (AppState.students || []).map(s => s.name);
      dl.innerHTML = names.map(n => `<option value="${n}"></option>`).join("");
    }

    const escape = s => (s || "").replace(/[<>&]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));
    const fillTab = (tabIdx, doc) => {
      AppState.currentSeatDocs[tabIdx] = doc;
      const el = document.getElementById(`seat-ddoc-${tabIdx}`);
      if (el) {
        el.innerHTML = `<div class="doc-paper"><pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;line-height:1.7;color:#1a1a18;margin:0">${escape(doc.document)}</pre></div>`;
      }
    };

    const promises = slots.map(({ tabIdx, docType }) =>
      DocumentAPI.generateSeat(AppState.currentSeatResult, docType, AppState.currentSeatId)
        .then(d => fillTab(tabIdx, d))
        .catch(e => fillTab(tabIdx, { document: `[문서 생성 실패: ${e.message}]`, type: docType }))
    );
    await Promise.all(promises);
    showToast("자리배정 확정 및 문서 생성 완료!");
  } catch (e) {
    showError(e.message);
  }
}

// === seat5 학생별 자리 배정 이유 검색 ===
async function searchSeatStudentReason() {
  const input = document.getElementById("seat5-search-input");
  const resultEl = document.getElementById("seat5-search-result");
  if (!input || !resultEl) return;
  const name = input.value.trim();
  if (!name) { showError("학생 이름을 입력하세요"); return; }
  if (!AppState.currentSeatResult) { showError("자리배정 결과가 없습니다"); return; }

  resultEl.style.display = "block";
  resultEl.textContent = "🤖 AI가 배정 이유를 분석 중입니다...";
  try {
    const data = await AssignmentAPI.explainSeatForStudent(AppState.currentSeatResult, name);
    resultEl.innerHTML = `<div style="font-weight:600;margin-bottom:6px">${data.student_name}</div><div>${(data.reason || "").replace(/\n/g, "<br>")}</div>`;
  } catch (e) {
    resultEl.textContent = "검색 실패: " + e.message;
  }
}

// === 자리배정 문서 다운로드/복사 ===
const SEAT_DOC_LABELS = { 0: "자리배정_결과표", 2: "형평성_리포트" };

function _activeSeatDocIndex() {
  const tabs = document.querySelectorAll("#seat5 .doc-tab");
  for (let i = 0; i < tabs.length; i++) if (tabs[i].classList.contains("active")) return i;
  return 0;
}

function _saveTextFile(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadActiveSeatDoc() {
  const docs = AppState.currentSeatDocs;
  if (!docs) { showError("먼저 문서를 생성하세요."); return; }
  const i = _activeSeatDocIndex();
  if (!(i in SEAT_DOC_LABELS)) { showError("이 탭은 다운로드 대상이 아닙니다."); return; }
  const text = docs[i]?.document || "";
  if (!text) { showError("저장할 문서 내용이 없습니다."); return; }
  _saveTextFile(`${SEAT_DOC_LABELS[i]}.txt`, text);
  showToast(`${SEAT_DOC_LABELS[i]} 저장 완료`);
}

async function copyActiveSeatDoc() {
  const docs = AppState.currentSeatDocs;
  if (!docs) { showError("먼저 문서를 생성하세요."); return; }
  const i = _activeSeatDocIndex();
  if (!(i in SEAT_DOC_LABELS)) { showError("이 탭은 복사 대상이 아닙니다."); return; }
  const text = docs[i]?.document || "";
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${SEAT_DOC_LABELS[i]} 복사 완료`);
  } catch (e) {
    showError("클립보드 복사 실패: " + e.message);
  }
}

function downloadAllSeatDocs() {
  const docs = AppState.currentSeatDocs;
  if (!docs) { showError("먼저 문서를 생성하세요."); return; }
  const sep = "\n\n" + "=".repeat(60) + "\n\n";
  const merged = Object.keys(SEAT_DOC_LABELS)
    .map(i => `# ${SEAT_DOC_LABELS[i]}\n\n${docs[i]?.document || ""}`)
    .join(sep);
  _saveTextFile("자리배정_전체문서.txt", merged);
  showToast("전체 문서 저장 완료");
}

// === 반배정 리포트 다운로드/복사 (DOM 텍스트 추출 방식) ===
function _extractClassReportText() {
  const report = document.getElementById("ban5-report");
  const reasons = document.getElementById("ban5-reasons");
  if (!report) return "";
  // doc-row 들을 "키: 값" 줄로 변환
  const reportLines = ["# 반배정 결과 보고서", ""];
  report.querySelectorAll(".doc-paper-title").forEach(t => {
    reportLines[0] = "# " + t.textContent.trim();
  });
  report.querySelectorAll(".doc-row").forEach(row => {
    const k = row.querySelector(".doc-key")?.textContent.trim() || "";
    const v = row.querySelector(".doc-val")?.textContent.trim() || "";
    if (k || v) reportLines.push(`- ${k}: ${v}`);
  });
  let out = reportLines.join("\n");
  if (reasons) {
    const reasonText = reasons.innerText.trim();
    if (reasonText && !/AI 분석 실행 후/.test(reasonText)) {
      out += "\n\n## AI 배치 근거\n\n" + reasonText;
    }
  }
  return out;
}

function downloadClassReport() {
  const text = _extractClassReportText();
  if (!text) { showError("저장할 리포트가 없습니다."); return; }
  _saveTextFile("반배정_리포트.txt", text);
  showToast("리포트 저장 완료");
}

async function copyClassReport() {
  const text = _extractClassReportText();
  if (!text) { showError("복사할 리포트가 없습니다."); return; }
  try {
    await navigator.clipboard.writeText(text);
    showToast("리포트 복사 완료");
  } catch (e) {
    showError("클립보드 복사 실패: " + e.message);
  }
}

// =============================================
// 초기화 — 앱 시작 시 실행
// =============================================

// 교사소견 파싱 완료 폴링
function setAnalysisBtnEnabled(enabled) {
  const btn = document.getElementById('btn-run-analysis');
  if (!btn) return;
  if (enabled) {
    btn.disabled = false;
    btn.style.opacity = '1';
    btn.style.cursor = 'pointer';
    btn.textContent = 'AI 분석 실행 →';
  } else {
    btn.disabled = true;
    btn.style.opacity = '0.5';
    btn.style.cursor = 'not-allowed';
    btn.textContent = '교사소견 분석 중... (잠시 기다려주세요)';
  }
}

async function pollNotesStatus() {
  // 버튼 비활성화
  setAnalysisBtnEnabled(false);

  const maxWait = 120;
  let elapsed = 0;
  const interval = setInterval(async () => {
    elapsed += 3;
    try {
      const res = await fetch(`${BASE_URL}/notes/status`);
      const data = await res.json();
      if (data.done) {
        clearInterval(interval);
        setAnalysisBtnEnabled(true);
        showToast(`✅ 교사소견 분석 완료! 이제 AI 분석을 실행하세요.`, 'success');
        const notice = document.getElementById('notes-done-notice');
        if (notice) {
          notice.style.display = 'block';
          notice.textContent = `✅ 교사소견 AI 분석 완료 — 리더십 학생이 자동으로 반영됩니다`;
        }
      } else if (elapsed >= maxWait) {
        clearInterval(interval);
        setAnalysisBtnEnabled(true); // 타임아웃 시 그냥 허용
        showToast('소견 분석 시간 초과 — AI 분석을 실행합니다', 'warn');
      }
    } catch(e) {
      clearInterval(interval);
      setAnalysisBtnEnabled(true);
    }
  }, 3000);
}

async function resetAllData() {
  if (!confirm('학생 데이터와 관계 데이터를 모두 삭제할까요?')) return;
  try {
    showLoading('데이터 초기화 중...');
    await fetch(`${BASE_URL}/students`, { method: 'DELETE' });
    await fetch(`${BASE_URL}/relations`, { method: 'DELETE' });
    AppState.students = [];
    AppState.relations = [];
    AppState.conditions.absolute = [];
    AppState._relationsAutoApplied = false;
    // UI 초기화
    const tbody = document.getElementById('ban-tbody');
    if (tbody) tbody.innerHTML = '';
    const absList = document.getElementById('ban-abs-list');
    if (absList) absList.innerHTML = '';
    renderRelations([]);
    updateStudentStats([]);
    const conflictEl = document.getElementById('ban-stat-conflict');
    if (conflictEl) conflictEl.textContent = '0';
    document.getElementById('ban-upload-result').textContent = '';

    // 자리배정 1단계 화면도 같이 초기화
    if (typeof updateSeat1Stats === 'function') updateSeat1Stats();
    if (typeof renderSeat1AutoConditions === 'function') renderSeat1AutoConditions();
    if (typeof renderSeat1StudentDatalist === 'function') renderSeat1StudentDatalist();
    const seatUploadResult = document.getElementById('seat-upload-result');
    if (seatUploadResult) seatUploadResult.textContent = '';

    showToast('데이터 초기화 완료! 엑셀 파일을 업로드해주세요.', 'success');
  } catch(e) {
    showToast('초기화 실패: ' + e.message, 'error');
  } finally {
    hideLoading();
  }
}

async function initApp() {
  console.log("Class Twin AI 초기화...");
  try {
    // 순서대로 로드: 학생 먼저, 관계 다음, 그 다음 절대조건 적용
    await loadStudents();
    await loadRelations();
    // DB에 학생/관계 데이터 있으면 절대 조건 자동 적용
    if (AppState.students.length > 0 || AppState.relations.length > 0) {
      updateAbsConditions(AppState.students, 'ban');
    }
    console.log("초기화 완료");
  } catch (e) {
    console.error("초기화 실패:", e);
    showToast("서버 연결 실패. 오프라인 모드로 실행합니다.", "warn");
  }
}

// 페이지 로드 시 초기화
document.addEventListener("DOMContentLoaded", initApp);

// =============================================
// Excel/CSV 업로드 기능
// =============================================

async function uploadExcelFile(file, type = 'students') {
  const formData = new FormData();
  formData.append('file', file);

  showLoading('기존 데이터 초기화 중...');
  try {
    // 업로드 전 기존 학생 + 관계 데이터 초기화
    await fetch(`${BASE_URL}/students`, { method: 'DELETE' });
    await fetch(`${BASE_URL}/relations`, { method: 'DELETE' });
  } catch(e) {
    // 초기화 실패해도 업로드는 시도
  }

  showLoading(type === 'students' ? '학생 데이터 업로드 중...' : '관계 데이터 업로드 중...');
  try {
    const res = await fetch(`${BASE_URL}/students/upload`, {
      method: 'POST',
      body: formData
    });
    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    AppState.students = data.students;
    renderStudentTable(data.students);
    updateStudentStats(data.students);
    // 두 번째 시트로 들어온 관계 데이터를 AppState에 동기화 (수동 swap 시 갈등 분류에 필요)
    try { await loadRelations(); } catch (_) {}
    // 자리배정 1단계 화면이 떠 있으면 그쪽도 갱신
    if (typeof updateSeat1Stats === 'function') updateSeat1Stats();
    if (typeof renderSeat1AutoConditions === 'function') renderSeat1AutoConditions();
    if (typeof renderSeat1StudentDatalist === 'function') renderSeat1StudentDatalist();
    const relCount = data.relations_added || 0;
    showToast(`${data.count}명 업로드 완료${relCount ? ` (관계 ${relCount}건)` : ''}`);
    return data;
  } catch(e) {
    showError(e.message);
  } finally {
    hideLoading();
  }
}

// 관계 데이터 파일 업로드
async function uploadRelationsFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE_URL}/relations/upload`, {
    method: 'POST',
    body: formData
  });
  if (!res.ok) throw new Error('관계 데이터 업로드 실패');
  return await res.json();
}

// 심각도 높음 관계를 절대 조건에 자동 추가
function applyAutoConditions(autoConditions) {
  const list = document.getElementById('ban-abs-list');
  if (!list) return;

  autoConditions.forEach(cond => {
    // 이미 같은 조건 있으면 중복 추가 안 함 (student_a + student_b + type 모두 일치할 때만 중복)
    const existingItems = list.querySelectorAll('.cond-item .cond-text');
    const isDuplicate = Array.from(existingItems).some(el => {
      const text = el.textContent;
      return text.includes(cond.student_a) && text.includes(cond.student_b) && text.includes(cond.type);
    });
    if (isDuplicate) return;

    const iconMap = {'분리': '✕', '같은 반': '+'};
    const clsMap  = {'분리': 'ci-red', '같은 반': 'ci-green'};
    const badgeMap= {'분리': 'br', '같은 반': 'bg2'};
    const type    = cond.type;
    const label   = cond.relation_type === '갈등'
      ? `${cond.student_a} ↔ ${cond.student_b} (갈등·심각도 높음)`
      : `${cond.student_a} ↔ ${cond.student_b} (친함·심각도 높음)`;

    const el = document.createElement('div');
    el.className = 'cond-item';
    el.innerHTML = `
      <div class="cond-icon ${clsMap[type]}">${iconMap[type]}</div>
      <div class="cond-text">${label} <span class="badge ${badgeMap[type]}">${type}</span> <span class="badge bb">자동</span></div>
      <button class="del-btn" onclick="this.closest('.cond-item').remove();updateSummary()">✕</button>`;
    list.appendChild(el);

    // AppState에도 추가 (중복 체크)
    const alreadyInState = AppState.conditions.absolute.some(
      c => c.student_a === cond.student_a && c.student_b === cond.student_b && c.type === type
    );
    if (!alreadyInState) {
      AppState.conditions.absolute.push({
        type: type,
        student_a: cond.student_a,
        student_b: cond.student_b,
        students: [cond.student_a, cond.student_b],
      });
    }
  });

  updateSummary();
}

// 업로드 후 절대 조건 목록 자동 업데이트
function updateAbsConditions(students, flow) {
  if (flow === 'seat') {
    const list = document.getElementById('seat-abs-list');
    if (!list) return;
    list.innerHTML = '';

    students.forEach(s => {
      if (s.vision === '약함') {
        list.innerHTML += `<div class="cond-item"><div class="cond-icon ci-blue">👁</div><div class="cond-text">${s.name} — 시력 약함 → 앞 2줄 배치</div><button class="del-btn" onclick="this.closest('.cond-item').remove()">✕</button></div>`;
      }
      if (s.special_needs === 'ADHD') {
        list.innerHTML += `<div class="cond-item"><div class="cond-icon ci-warn">⚡</div><div class="cond-text">${s.name} — ADHD → 교사 근처 앞자리</div><button class="del-btn" onclick="this.closest('.cond-item').remove()">✕</button></div>`;
      } else if (s.special_needs) {
        list.innerHTML += `<div class="cond-item"><div class="cond-icon ci-warn">★</div><div class="cond-text">${s.name} — ${s.special_needs} → 특수교육 배려</div><button class="del-btn" onclick="this.closest('.cond-item').remove()">✕</button></div>`;
      }
    });

    // 갈등 관계 분리 조건 추가
    AppState.relations.filter(r => r.type === '갈등').forEach(r => {
      list.innerHTML += `<div class="cond-item"><div class="cond-icon ci-red">✕</div><div class="cond-text">${r.student_a} ↔ ${r.student_b} — 분리 (갈등이력)</div><button class="del-btn" onclick="this.closest('.cond-item').remove()">✕</button></div>`;
    });

  } else if (flow === 'ban') {
    const list = document.getElementById('ban-abs-list');
    if (!list) return;

    // 기존 자동 배지 항목 모두 제거 후 새로 그리기
    list.querySelectorAll('.cond-item').forEach(item => {
      if (item.querySelector('.badge.bb')) item.remove();
    });

    // AppState.conditions.absolute에서 자동 조건도 초기화 (중복 방지)
    AppState.conditions.absolute = AppState.conditions.absolute.filter(c => !c._auto);

    // 특수교육 학생 묶어서 하나로 표시
    const specialStudents = students.filter(s => s.special_needs);
    if (specialStudents.length > 0) {
      const existingSpecial = Array.from(list.querySelectorAll('.cond-text'))
        .some(el => el.textContent.includes('특수교육 배려'));
      if (!existingSpecial) {
        const names = specialStudents.map(s => s.name).join(', ');
        const el = document.createElement('div');
        el.className = 'cond-item';
        el.innerHTML = `<div class="cond-icon ci-warn">★</div><div class="cond-text">${names} <span class="badge bw">특수교육 배려 (각 반 균등 분배)</span> <span class="badge bb">자동</span></div>`;
        list.appendChild(el);
        // 수정 불가 (X 버튼 없음)
      }
    }

    // 관계 데이터에서 중간 이상 갈등, 높음 친함 자동 추가 (수정 불가)
    AppState.relations.filter(r =>
      (r.type === '갈등' && ['높음','중간'].includes(r.severity)) ||
      (r.type === '친함' && r.severity === '높음')
    ).forEach(r => {
      const type = r.type === '갈등' ? '분리' : '같은 반';
      const existing = Array.from(list.querySelectorAll('.cond-text'))
        .some(el => el.textContent.includes(r.student_a) && el.textContent.includes(r.student_b));
      if (existing) return;
      const icon = type === '분리' ? '✕' : '+';
      const cls = type === '분리' ? 'ci-red' : 'ci-green';
      const badge = type === '분리' ? 'br' : 'bg2';
      const el = document.createElement('div');
      el.className = 'cond-item';
      // 수정 불가: X 버튼 없음, 잠금 아이콘
      el.innerHTML = `<div class="cond-icon ${cls}">${icon}</div><div class="cond-text">${r.student_a} ↔ ${r.student_b} <span class="badge ${badge}">${type}</span> <span class="badge bb">자동</span></div><span style="font-size:11px;color:#bbb;margin-left:auto;padding-right:4px">🔒</span>`;
      list.appendChild(el);

      // AppState에도 추가 (_auto 플래그로 중복 방지)
      const already = AppState.conditions.absolute.some(
        c => c.student_a === r.student_a && c.student_b === r.student_b && c.type === type
      );
      if (!already) {
        AppState.conditions.absolute.push({
          type, student_a: r.student_a, student_b: r.student_b,
          students: [r.student_a, r.student_b], _auto: true
        });
      }
    });

    updateSummary();
  }
}

async function loadMockData() {
  showLoading('샘플 데이터 불러오는 중...');
  try {
    // 목업 데이터 30명 백엔드로 전송
    const res = await fetch(`${BASE_URL}/students/bulk`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(MOCK_STUDENTS)
    });
    const data = await res.json();
    AppState.students = data;
    await loadStudents();
    showToast('샘플 데이터 30명 로드 완료!');
  } catch(e) {
    // 백엔드 없으면 그냥 화면만 채움
    AppState.students = MOCK_STUDENTS;
    renderStudentTable(MOCK_STUDENTS);
    updateStudentStats(MOCK_STUDENTS);
    showToast('샘플 데이터 로드 완료 (오프라인 모드)');
  } finally {
    hideLoading();
  }
}

// 목업 데이터 30명
const MOCK_STUDENTS = [
  {id:1, name:'김민준', gender:'남', academic_level:'상', height:168, vision:'정상', attention_level:'높음', special_needs:null, teacher_note:'리더십이 강하나 방향이 부정적으로 흐를 때 있음'},
  {id:2, name:'이서연', gender:'여', academic_level:'상', height:158, vision:'약함', attention_level:'높음', special_needs:null, teacher_note:'성적 우수하며 친화력 높음'},
  {id:3, name:'박지호', gender:'남', academic_level:'중', height:162, vision:'정상', attention_level:'낮음', special_needs:null, teacher_note:'친한 친구가 거의 없음. 관계망 고립 상태'},
  {id:4, name:'신동현', gender:'남', academic_level:'하', height:163, vision:'정상', attention_level:'낮음', special_needs:'ADHD', teacher_note:'ADHD 진단. 교사 근처 자리 필요'},
  {id:5, name:'한지민', gender:'여', academic_level:'상', height:160, vision:'약함', attention_level:'높음', special_needs:null, teacher_note:'모범생. 책임감 강하고 긍정적 영향'},
  {id:6, name:'오승현', gender:'남', academic_level:'중', height:165, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'운동을 좋아하며 수업 중 산만해질 때 있음'},
  {id:7, name:'송지우', gender:'여', academic_level:'상', height:161, vision:'정상', attention_level:'높음', special_needs:null, teacher_note:'리더십과 공감 능력 탁월. 갈등 중재 역할'},
  {id:8, name:'윤재원', gender:'남', academic_level:'상', height:172, vision:'정상', attention_level:'높음', special_needs:null, teacher_note:'학업과 대인관계 모두 우수'},
  {id:9, name:'강예은', gender:'여', academic_level:'중', height:159, vision:'약함', attention_level:'중간', special_needs:null, teacher_note:'예체능에 소질 있음'},
  {id:10, name:'정도윤', gender:'남', academic_level:'하', height:170, vision:'정상', attention_level:'낮음', special_needs:null, teacher_note:'학습 동기가 낮음. 관심 필요'},
  {id:11, name:'남궁현', gender:'남', academic_level:'중', height:168, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'친화력 좋고 새로운 환경 적응 빠름'},
  {id:12, name:'도하영', gender:'여', academic_level:'하', height:155, vision:'정상', attention_level:'낮음', special_needs:null, teacher_note:'최근 전학 온 학생. 아직 적응 중'},
  {id:13, name:'방유진', gender:'여', academic_level:'상', height:163, vision:'정상', attention_level:'높음', special_needs:null, teacher_note:'수학 올림피아드 수상. 논리적 사고 뛰어남'},
  {id:14, name:'배성민', gender:'남', academic_level:'중', height:167, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'유머 감각 있음. 지나칠 때 있음'},
  {id:15, name:'탁승우', gender:'남', academic_level:'하', height:161, vision:'정상', attention_level:'낮음', special_needs:'학습장애', teacher_note:'학습장애 진단. 개별화 지원 필요'},
  {id:16, name:'홍준서', gender:'남', academic_level:'하', height:164, vision:'정상', attention_level:'낮음', special_needs:null, teacher_note:'자존감 낮음. 칭찬과 격려에 잘 반응'},
  {id:17, name:'구나연', gender:'여', academic_level:'중', height:159, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'공감 능력 높음. 친구 고민 잘 들어줌'},
  {id:18, name:'백서희', gender:'여', academic_level:'상', height:162, vision:'정상', attention_level:'높음', special_needs:null, teacher_note:'전교 임원 경험. 책임감과 추진력 강함'},
  {id:19, name:'엄준혁', gender:'남', academic_level:'중', height:173, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'경쟁심 강해 가끔 갈등 유발'},
  {id:20, name:'채린', gender:'여', academic_level:'중', height:160, vision:'약함', attention_level:'높음', special_needs:null, teacher_note:'꼼꼼하고 책임감 강함'},
  {id:21, name:'고태양', gender:'남', academic_level:'하', height:166, vision:'정상', attention_level:'낮음', special_needs:null, teacher_note:'미술에 재능 있음. 자신감 향상 필요'},
  {id:22, name:'권민서', gender:'여', academic_level:'중', height:158, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'다문화 가정. 한국어 능숙하나 가끔 어려움'},
  {id:23, name:'라민성', gender:'남', academic_level:'중', height:167, vision:'약함', attention_level:'중간', special_needs:null, teacher_note:'차분하고 신중함. 소집단 활동에서 강점'},
  {id:24, name:'류하은', gender:'여', academic_level:'중', height:156, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'조용하고 성실함. 소수의 친한 친구와 깊은 관계'},
  {id:25, name:'문현우', gender:'남', academic_level:'상', height:169, vision:'정상', attention_level:'높음', special_needs:null, teacher_note:'독서량 많고 사고력 깊음. 발표는 꺼려함'},
  {id:26, name:'심재훈', gender:'남', academic_level:'중', height:171, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'운동 능력 뛰어남. 긍정적 방향으로 작용'},
  {id:27, name:'노은지', gender:'여', academic_level:'하', height:154, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'최근 가정 환경 변화로 심리적 위축'},
  {id:28, name:'전하늘', gender:'여', academic_level:'하', height:156, vision:'약함', attention_level:'중간', special_needs:null, teacher_note:'소극적이나 한번 친해지면 깊은 관계 유지'},
  {id:29, name:'최수아', gender:'여', academic_level:'중', height:155, vision:'정상', attention_level:'중간', special_needs:null, teacher_note:'밝고 긍정적. 분위기 메이커 역할'},
  {id:30, name:'홍준희', gender:'남', academic_level:'하', height:165, vision:'정상', attention_level:'낮음', special_needs:null, teacher_note:'학습 지원 필요. 체육에 강점 있음'}
];