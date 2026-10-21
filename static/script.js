const courses = [
  { name: "Python Programming", category: "Coding fundamentals", emoji: "⌘", progress: 75, description: "Build confidence with Python fundamentals, problem-solving, and practical projects." },
  { name: "Database Management", category: "Data & systems", emoji: "▤", progress: 50, description: "Learn how data is structured, queried, and managed in modern applications." },
  { name: "Web Development", category: "Creative technology", emoji: "✳", progress: 90, description: "Explore the building blocks behind the websites and experiences you use every day." },
  { name: "Artificial Intelligence", category: "Emerging technology", emoji: "✦", progress: 35, description: "Get curious about AI concepts, machine learning, and responsible innovation." }
];

const pageTitles = {
  dashboard: "Overview",
  courses: "My courses",
  classrooms: "Classes",
  assignments: "Assignments",
  quizzes: "Quick quiz",
  analytics: "Progress",
  assistant: "AI study buddy"
};
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const userRole = document.body.dataset.role;
let assignments = [];
let classrooms = [];
let analyticsChart;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

async function apiRequest(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Something went wrong. Please try again.");
  }
  return payload;
}

function showPage(page) {
  document.querySelectorAll(".page-section").forEach(section => {
    section.classList.toggle("hidden", section.id !== page);
  });
  document.querySelectorAll(".nav-item").forEach(button => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  document.getElementById("breadcrumbPage").textContent = pageTitles[page] || "Overview";
  if (page === "analytics") renderAnalytics();
  if (page === "assignments") loadAssignments();
  if (page === "classrooms") loadClassrooms();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadClassrooms() {
  const list = document.getElementById("classroomList");
  list.innerHTML = '<p class="empty-state">Loading your classes…</p>';
  try {
    const result = await apiRequest("/api/classrooms");
    classrooms = result.classrooms;
    document.getElementById("classroomPageTitle").textContent =
      userRole === "instructor" ? "My classrooms" : "My classes";
    document.getElementById("classroomPageSubtitle").textContent =
      userRole === "instructor"
        ? "Create a class, share its code, and post assignments for your students."
        : "Join a class with your instructor’s code to see work and submit assignments.";
    if (!classrooms.length) {
      list.innerHTML = `<div class="content-card empty-classroom"><span class="stat-icon lavender">▣</span><h2>${userRole === "instructor" ? "Create your first class" : "No classes yet"}</h2><p>${userRole === "instructor" ? "Create a classroom and share its join code with students." : "Ask your instructor for a class code, then join above."}</p></div>`;
      return;
    }
    list.innerHTML = classrooms.map(classroom => `
      <article class="content-card classroom-card" data-classroom-id="${classroom.id}">
        <div class="classroom-heading">
          <div><p class="eyebrow">${userRole === "instructor" ? "YOUR CLASS" : "ENROLLED CLASS"}</p><h2>${escapeHtml(classroom.name)}</h2></div>
          ${userRole === "instructor" ? `<span class="join-code"><small>CLASS CODE</small><strong>${escapeHtml(classroom.join_code)}</strong></span>` : ""}
        </div>
        <p class="classroom-description">${escapeHtml(classroom.description || "Welcome to class. Check assignments and due dates below.")}</p>
        <div class="classroom-meta"><span>${userRole === "instructor" ? `${classroom.student_count} students` : "Assignments and materials"}</span><span>${userRole === "instructor" ? "Share the code to invite students" : "Your instructor manages coursework"}</span></div>
        <div class="classroom-assignments" data-assignment-list="${classroom.id}"><p class="empty-state">Loading assignments…</p></div>
        ${userRole === "instructor" ? `
          <details class="post-assignment">
            <summary>＋ Post an assignment</summary>
            <form class="class-assignment-form" data-classroom-id="${classroom.id}">
              <label>Assignment title<input name="title" maxlength="200" placeholder="e.g. Chapter 1 worksheet" required></label>
              <label>Instructions<textarea name="instructions" maxlength="5000" rows="3" placeholder="Describe what students should do"></textarea></label>
              <label>Due date<input name="due_date" type="date" required></label>
              <label>Reference file <span class="inline-hint">optional · up to 15 MB</span><input name="file" type="file" accept=".pdf,.ppt,.pptx,.doc,.docx,.txt,.csv,.xlsx,.xls,.png,.jpg,.jpeg,.zip,.md,.rtf"></label>
              <button class="button button-dark" type="submit">Publish assignment <span>→</span></button>
              <p class="form-message" role="status"></p>
            </form>
          </details>` : ""}
      </article>`).join("");

    await Promise.all(classrooms.map(classroom => loadClassroomAssignments(classroom.id)));
  } catch (error) {
    list.innerHTML = `<p class="form-message">${escapeHtml(error.message)}</p>`;
  }
}

async function loadClassroomAssignments(classroomId) {
  const container = document.querySelector(`[data-assignment-list="${classroomId}"]`);
  if (!container) return;
  try {
    const result = await apiRequest(`/api/classrooms/${classroomId}/assignments`);
    if (!result.assignments.length) {
      container.innerHTML = '<p class="empty-state">No assignments posted yet.</p>';
      return;
    }
    container.innerHTML = result.assignments.map(assignment => `
      <article class="class-assignment" data-class-assignment="${assignment.id}">
        <div class="assignment-copy">
          <strong>${escapeHtml(assignment.title)}</strong>
          <span>Due ${escapeHtml(new Date(`${assignment.due_date}T00:00:00`).toLocaleDateString())}${userRole === "instructor" ? ` · ${assignment.submission_count} submitted` : assignment.my_submitted_at ? " · Submitted" : ""}</span>
          ${assignment.instructions ? `<p>${escapeHtml(assignment.instructions)}</p>` : ""}
          ${assignment.attachment_name ? `<a class="small-button" href="/api/classroom-files/assignment/${assignment.id}">↧ ${escapeHtml(assignment.attachment_name)}</a>` : ""}
        </div>
        ${userRole === "student" ? `
          <form class="submission-form" data-assignment-id="${assignment.id}">
            <input name="file" type="file" accept=".pdf,.ppt,.pptx,.doc,.docx,.txt,.csv,.xlsx,.xls,.png,.jpg,.jpeg,.zip,.md,.rtf" required aria-label="Choose assignment file">
            <button class="small-button" type="submit">${assignment.my_submitted_at ? "Replace submission" : "Submit work"}</button>
            ${assignment.my_filename ? `<a class="small-button" href="/api/classroom-files/submission/${assignment.my_submission_id}">View submitted: ${escapeHtml(assignment.my_filename)}</a>` : ""}
            <span class="form-message" role="status"></span>
          </form>` : `<button class="small-button" data-view-submissions="${assignment.id}">View submissions</button><div class="submission-list" data-submissions="${assignment.id}"></div>`}
      </article>`).join("");
  } catch (error) {
    container.innerHTML = `<p class="form-message">${escapeHtml(error.message)}</p>`;
  }
}

async function handleClassroomFormSubmit(event) {
  const form = event.target;
  if (form.id === "createClassForm") {
    event.preventDefault();
    const message = form.querySelector(".form-message");
    message.textContent = "";
    try {
      await apiRequest("/api/classrooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form)))
      });
      form.reset();
      message.textContent = "Class created. Share its class code with students.";
      await loadClassrooms();
    } catch (error) {
      message.textContent = error.message;
    }
    return;
  }
  if (form.id === "joinClassForm") {
    event.preventDefault();
    const message = form.querySelector(".form-message");
    message.textContent = "";
    try {
      await apiRequest("/api/classrooms/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ join_code: new FormData(form).get("join_code") })
      });
      form.reset();
      message.textContent = "You joined the class!";
      await loadClassrooms();
    } catch (error) {
      message.textContent = error.message;
    }
    return;
  }
  if (form.matches(".class-assignment-form")) {
    event.preventDefault();
    const message = form.querySelector(".form-message");
    message.textContent = "";
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await apiRequest(`/api/classrooms/${form.dataset.classroomId}/assignments`, {
        method: "POST",
        body: new FormData(form)
      });
      form.reset();
      message.textContent = "Assignment published to this class.";
      await loadClassroomAssignments(form.dataset.classroomId);
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (form.matches(".submission-form")) {
    event.preventDefault();
    const message = form.querySelector(".form-message");
    message.textContent = "";
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const result = await apiRequest(`/api/classroom-assignments/${form.dataset.assignmentId}/submit`, {
        method: "POST",
        body: new FormData(form)
      });
      message.textContent = `Submitted: ${result.filename}`;
      const classroom = form.closest("[data-classroom-id]");
      await loadClassroomAssignments(classroom.dataset.classroomId);
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }
}

async function loadSubmissions(assignmentId, button) {
  const container = document.querySelector(`[data-submissions="${assignmentId}"]`);
  try {
    const result = await apiRequest(`/api/classroom-assignments/${assignmentId}/submissions`);
    container.innerHTML = result.submissions.length
      ? result.submissions.map(item => `<div class="submission-row"><span>${escapeHtml(item.student_name)} (@${escapeHtml(item.username)}) · ${escapeHtml(new Date(item.submitted_at).toLocaleString())}</span><a class="small-button" href="/api/classroom-files/submission/${item.id}">↧ ${escapeHtml(item.filename)}</a></div>`).join("")
      : '<p class="empty-state">No students have submitted yet.</p>';
    button.textContent = "Refresh submissions";
  } catch (error) {
    container.innerHTML = `<p class="form-message">${escapeHtml(error.message)}</p>`;
  }
}

function renderCourses() {
  const courseMarkup = courses.map(course => `
    <article class="course-row">
      <span class="course-emoji" aria-hidden="true">${course.emoji}</span>
      <div class="course-copy"><strong>${escapeHtml(course.name)}</strong><span>${escapeHtml(course.category)}</span></div>
      <div class="progress-track" role="progressbar" aria-label="${escapeHtml(course.name)} progress" aria-valuenow="${course.progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${course.progress}%"></span></div>
      <span class="course-percent">${course.progress}%</span>
    </article>`).join("");
  document.getElementById("courseList").innerHTML = courseMarkup;
  document.getElementById("allCourses").innerHTML = courses.map(course => `
    <article class="content-card all-course-card">
      <span class="course-emoji" aria-hidden="true">${course.emoji}</span>
      <p class="eyebrow">${escapeHtml(course.category)}</p>
      <h2>${escapeHtml(course.name)}</h2>
      <p>${escapeHtml(course.description)}</p>
      <div class="course-progress-label"><span>Course progress</span><strong>${course.progress}%</strong></div>
      <div class="progress-track" role="progressbar" aria-label="${escapeHtml(course.name)} progress" aria-valuenow="${course.progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${course.progress}%"></span></div>
    </article>`).join("");
  document.getElementById("courseCount").textContent = courses.length;
}

function dueLabel(dateString) {
  const due = new Date(`${dateString}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((due - today) / 86400000);
  if (diff < 0) return "Overdue";
  if (diff === 0) return "Due today";
  if (diff === 1) return "Due tomorrow";
  return `Due in ${diff} days`;
}

function formatDate(dateString) {
  const date = new Date(`${dateString}T00:00:00`);
  return {
    day: date.toLocaleDateString(undefined, { day: "2-digit" }),
    month: date.toLocaleDateString(undefined, { month: "short" })
  };
}

function renderAssignments() {
  const openCount = assignments.filter(item => !item.completed).length;
  document.getElementById("taskCount").textContent =
    `${assignments.filter(item => item.completed).length}/${assignments.length}`;
  document.getElementById("navAssignmentCount").textContent = openCount;

  const upcoming = assignments.filter(item => !item.completed)
    .sort((a, b) => a.due_date.localeCompare(b.due_date)).slice(0, 3);
  document.getElementById("upcomingList").innerHTML = upcoming.length
    ? upcoming.map(item => {
      const date = formatDate(item.due_date);
      return `<article class="upcoming-item"><span class="date-chip"><strong>${date.day}</strong><span>${date.month}</span></span><div class="upcoming-copy"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(dueLabel(item.due_date))}</span></div><span class="due-tag">${escapeHtml(date.month)} ${date.day}</span></article>`;
    }).join("")
    : '<p class="empty-state">No upcoming tasks yet. Add one when you’re ready ✨</p>';

  document.getElementById("assignmentList").innerHTML = assignments.length
    ? assignments.map(item => {
      const date = formatDate(item.due_date);
      const attachment = item.filename
        ? `<a class="small-button" href="/api/assignments/${item.id}/download">↧ ${escapeHtml(item.filename)}</a>`
        : "";
      const completeButton = item.completed ? "" : `<button class="small-button" data-complete="${item.id}">Mark done</button>`;
      return `<article class="assignment-item ${item.completed ? "is-done" : ""}">
        <span class="assignment-icon" aria-hidden="true">${item.completed ? "✓" : "▤"}</span>
        <div class="assignment-copy"><strong>${escapeHtml(item.title)}</strong><span>${item.completed ? "Completed" : `${escapeHtml(dueLabel(item.due_date))} · ${date.month} ${date.day}`}${item.filename ? " · File attached" : ""}</span></div>
        <div class="assignment-actions">${attachment}${completeButton}<button class="small-button danger" data-delete="${item.id}" aria-label="Delete ${escapeHtml(item.title)}">Delete</button></div>
      </article>`;
    }).join("")
    : '<p class="empty-state">Your list is clear. Add a personal task to get started ✨</p>';
}

async function loadAssignments() {
  try {
    const result = await apiRequest("/api/assignments");
    assignments = result.assignments;
    renderAssignments();
  } catch (error) {
    document.getElementById("assignmentMessage").textContent = error.message;
  }
}

async function addAssignment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.getElementById("assignmentMessage");
  message.textContent = "";
  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  const formData = new FormData(form);
  try {
    const result = await apiRequest("/api/assignments", { method: "POST", body: formData });
    assignments.unshift(result.assignment);
    form.reset();
    document.getElementById("fileName").textContent = "No file selected";
    renderAssignments();
    message.textContent = "Task added. You’ve got this!";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
}

async function changeAssignment(id, action) {
  try {
    await apiRequest(`/api/assignments/${id}/${action}`, { method: "POST" });
    await loadAssignments();
  } catch (error) {
    document.getElementById("assignmentMessage").textContent = error.message;
  }
}

function answerQuiz(answer) {
  const correct = answer === "Python";
  document.getElementById("quizResult").textContent =
    correct ? "That’s it! Flask is a Python web framework. ✨" : "Not quite — Flask is built with Python. Give it another go!";
  document.getElementById("quizResult").style.color = correct ? "#3b9a79" : "#b26a47";
}

function createChart() {
  if (!window.Chart) return;
  if (analyticsChart) analyticsChart.destroy();
  analyticsChart = new Chart(document.getElementById("analyticsChart"), {
    type: "bar",
    data: {
      labels: courses.map(course => course.name),
      datasets: [{
        label: "Sample course progress",
        data: courses.map(course => course.progress),
        borderColor: "#8064df",
        backgroundColor: ["#8064df", "#55c3a1", "#f1a776", "#76a8e4"],
        borderRadius: 6,
        borderSkipped: false
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, max: 100, grid: { color: "#f1eff6" }, ticks: { color: "#9997a7", font: { size: 9 } } },
        x: { grid: { display: false }, ticks: { color: "#9997a7", font: { size: 9 } } }
      }
    }
  });
}

function renderAnalytics() {
  createChart();
  const average = Math.round(courses.reduce((sum, course) => sum + course.progress, 0) / courses.length);
  document.getElementById("analyticsSummary").textContent =
    `The starter course catalog averages ${average}% sample progress. Personal course enrollment and activity tracking are not connected yet.`;
}

function addChatMessage(content, role) {
  const message = document.createElement("div");
  message.className = `chat-message ${role === "user" ? "user-message" : "assistant-message"}`;
  const safeContent = escapeHtml(content).replace(/\n/g, "<br>");
  message.innerHTML = `${role === "assistant" ? '<span class="message-avatar">✦</span>' : ""}<div><p>${safeContent}</p><small>${role === "user" ? "You" : "Study buddy"}</small></div>`;
  const messages = document.getElementById("chatMessages");
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
}

async function sendChatMessage(event) {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  const notice = document.getElementById("chatNotice");
  const button = event.currentTarget.querySelector("button");
  addChatMessage(message, "user");
  input.value = "";
  notice.textContent = "";
  button.disabled = true;
  try {
    const history = [...document.querySelectorAll("#chatMessages .chat-message")]
      .slice(-12)
      .map(item => ({
        role: item.classList.contains("user-message") ? "user" : "assistant",
        content: item.querySelector("p").textContent
      }));
    const result = await apiRequest("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history })
    });
    addChatMessage(result.reply, "assistant");
  } catch (error) {
    notice.textContent = error.message;
  } finally {
    button.disabled = false;
    input.focus();
  }
}

document.querySelectorAll("[data-page]").forEach(button => {
  button.addEventListener("click", () => showPage(button.dataset.page));
});
document.getElementById("assignmentForm").addEventListener("submit", addAssignment);
document.getElementById("assignmentFile").addEventListener("change", event => {
  document.getElementById("fileName").textContent =
    event.target.files[0]?.name || "No file selected";
});
document.getElementById("assignmentList").addEventListener("click", event => {
  const target = event.target.closest("[data-complete], [data-delete]");
  if (!target) return;
  const action = target.dataset.complete ? "complete" : "delete";
  changeAssignment(target.dataset.complete || target.dataset.delete, action);
});
document.querySelectorAll(".quiz-option").forEach(button => {
  button.addEventListener("click", () => answerQuiz(button.dataset.answer));
});
document.getElementById("chatForm").addEventListener("submit", sendChatMessage);
document.getElementById("classrooms").addEventListener("submit", handleClassroomFormSubmit);
document.getElementById("classrooms").addEventListener("click", event => {
  const button = event.target.closest("[data-view-submissions]");
  if (button) loadSubmissions(button.dataset.viewSubmissions, button);
});
document.getElementById("todayLabel").textContent =
  new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
document.getElementById("welcomeDate").textContent =
  new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }).toUpperCase();

renderCourses();
loadAssignments();
