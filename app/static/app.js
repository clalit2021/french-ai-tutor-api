/* Minimal frontend to drive the API */
const $ = (sel) => document.querySelector(sel);
const statusEl = $("#status");
const asyncLogEl = $("#asyncLog");
const asyncLessonEl = $("#asyncLesson");
const syncLessonEl = $("#syncLesson");
const imagePreviewEl = $("#imagePreview");
const imageTabsEl = $("#imageTabs");
const chatBox = $("#chatBox");

let lastSyncLesson = null;   // cache for chat
let chatHistory = [];        // [{role, content}]
let selectedImage = null;

function setStatus(msg) {
  statusEl.textContent = msg;
}

function logAsync(msg) {
  const p = document.createElement("div");
  p.textContent = msg;
  asyncLogEl.prepend(p);
}

function bearerHeader() {
  const t = $("#authToken").value.trim();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

function renderUiSteps(container, ui) {
  container.innerHTML = "";
  if (!ui || !Array.isArray(ui)) return;
  ui.forEach((block) => {
    const card = document.createElement("div");
    card.className = "step";

    if (block.type === "image_card") {
      const img = document.createElement("img");
      img.alt = block.text || "image";
      img.src = block.image_url || block.data_url || "";
      const cap = document.createElement("div");
      cap.textContent = block.text || "";
      card.appendChild(img);
      card.appendChild(cap);
    } else if (block.type === "question") {
      const q = document.createElement("div");
      q.className = "q";
      q.textContent = block.question || "";
      const opts = document.createElement("div");
      opts.className = "opts";
      (block.options || []).forEach((opt, i) => {
        const btn = document.createElement("button");
        btn.className = "btn-sm";
        btn.type = "button";
        btn.textContent = opt;
        btn.onclick = () => {
          const ok = i === (block.correct_option ?? -1);
          alert(ok ? "✅ Bravo !" : "❌ Essaie encore");
        };
        opts.appendChild(btn);
      });
      card.appendChild(q);
      card.appendChild(opts);
    } else if (block.type === "note") {
      card.textContent = block.text || "";
    } else {
      // fallback
      card.textContent = block.text || JSON.stringify(block);
    }
    container.appendChild(card);
  });
}

function pushChat(role, content, speak = false) {
  const bubble = document.createElement("div");
  bubble.className = role === "user" ? "bubble user" : "bubble bot";
  bubble.textContent = content;
  chatBox.appendChild(bubble);
  chatBox.scrollTop = chatBox.scrollHeight;
  if (speak && role === "bot") speakText(content);
}

function speakText(text) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.95; u.pitch = 1.0; u.lang = "fr-FR";
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  } catch (e) {
    // ignore
  }
}

// --- Simple TTS helpers (Web Speech API) ---
function speak(text, { rate = 0.9 } = {}) {
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "fr-FR";
    u.rate = rate;
    speechSynthesis.speak(u);
  } catch (e) {
    console.warn("TTS failed:", e);
  }
}

// Pick an image URL by id (if your lesson_data.images exists)
function findImageUrl(lesson, imageRef) {
  if (!lesson?.images?.length) return null;
  const byId = lesson.images.find(x => x.id === imageRef);
  if (byId?.url) return byId.url;
  return null;
}

// Build one card node
function card({ title, text, imgUrl, slowText }) {
  const el = document.createElement("article");
  el.className = "card";
  el.innerHTML = `
    <h3>${title}</h3>
    ${imgUrl ? `<img alt="" src="${imgUrl}">` : ""}
    <p class="small">${text}</p>
    <div class="row">
      <button class="tts normal">🔊 Lire</button>
      <button class="tts slow">🐢 Lent</button>
    </div>
  `;
  const btnNormal = el.querySelector(".tts.normal");
  const btnSlow = el.querySelector(".tts.slow");
  btnNormal.onclick = () => speak(slowText ?? text, { rate: 0.95 });
  btnSlow.onclick   = () => speak(slowText ?? text, { rate: 0.8 });
  return el;
}

// Render the whole lesson (expects your parsed lesson_data JSON)
function renderLessonCards(lesson) {
  const root = document.getElementById("lessonView");
  if (!root) return;
  root.innerHTML = "";

  const duration = lesson?.duration || "30 minutes";
  const objectives = (lesson?.objectives || []).slice(0,3).join(" • ");
  root.appendChild(card({
    title: lesson?.title || "Leçon",
    text: `Durée: ${duration}${objectives ? " — Objectifs: " + objectives : ""}`,
    imgUrl: findImageUrl(lesson, "img_cover")
  }));

  if (lesson?.vocab_cards?.length) {
    lesson.vocab_cards.forEach(vc => {
      const imgUrl = findImageUrl(lesson, vc.image_ref);
      root.appendChild(card({
        title: `Carte: ${vc.word}`,
        text: `Répète: « ${vc.word} »`,
        imgUrl,
        slowText: vc.word
      }));
    });
  }

  if (lesson?.mini_story?.length) {
    const storyLines = lesson.mini_story.join(" ");
    root.appendChild(card({
      title: "Petite histoire",
      text: storyLines,
      imgUrl: findImageUrl(lesson, "img_story"),
      slowText: storyLines
    }));
  }

  if (lesson?.phonics) {
    const ph = lesson.phonics;
    const text = `Son: « ${ph.grapheme} ». Exemples: ${ph.examples?.join(", ") || ""}. ${ph.tip || ""}`;
    root.appendChild(card({
      title: "Sons en français",
      text,
      imgUrl: findImageUrl(lesson, "img_phonics"),
      slowText: text
    }));
  }

  if (lesson?.activities?.length) {
    lesson.activities.forEach((act, i) => {
      const desc = act.prompt || act.type || "Activité";
      root.appendChild(card({
        title: `Activité ${i+1}`,
        text: desc,
        imgUrl: null,
        slowText: desc
      }));
    });
  }

  if (lesson?.quiz?.length) {
    const qs = lesson.quiz.map((q,i)=> `${i+1}. ${q.q || q.prompt}`).join(" ");
    root.appendChild(card({
      title: "Quiz rapide",
      text: qs,
      imgUrl: null,
      slowText: qs
    }));
  }

  if (lesson?.homework) {
    root.appendChild(card({
      title: "Devoir",
      text: lesson.homework,
      imgUrl: findImageUrl(lesson, "img_reward"),
      slowText: lesson.homework
    }));
  }
}

/* =========================
   A) Async flow (Celery)
   ========================= */

$("#btnAsync").addEventListener("click", async () => {
  const child_id = $("#childId").value.trim();
  const file_path = $("#filePath").value.trim();
  if (!child_id || !file_path) {
    setStatus("❌ child_id et file_path requis");
    return;
  }

  setStatus("🚀 Envoi du job…");
  try {
    const res = await fetch("/api/lessons", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...bearerHeader() },
      body: JSON.stringify({ child_id, file_path }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    logAsync(`Job créé: ${data.lesson_id}`);
    setStatus("⏳ Traitement en cours…");

    // poll
    const id = data.lesson_id;
    const poll = async () => {
      const r = await fetch(`/api/lessons/${encodeURIComponent(id)}`, {
        headers: { ...bearerHeader() },
      });
      const j = await r.json();
      if (j.status === "completed") {
        setStatus("✅ Terminé");
        if (j.lesson && j.lesson.ui_steps) {
          renderUiSteps(asyncLessonEl, j.lesson.ui_steps);
        } else {
          asyncLessonEl.textContent = JSON.stringify(j.lesson || {}, null, 2);
        }
        if (j.lesson) {
          lastSyncLesson = j.lesson;
          renderLessonCards(j.lesson);
        }
        return;
      }
      if (j.status === "error") {
        setStatus("❌ Erreur dans le job");
        asyncLessonEl.textContent = JSON.stringify(j, null, 2);
        return;
      }
      setTimeout(poll, 2000);
    };
    poll();
  } catch (e) {
    setStatus("❌ " + e.message);
  }
});

/* =========================
   B) Sync lesson (Mimi)
   ========================= */

$("#btnBuild").addEventListener("click", async () => {
  const topic = $("#topic").value.trim();
  const age = parseInt($("#age").value || "11", 10);
  const pdf_text = $("#pdfText").value;

  setStatus("🧠 Construction de la leçon (sync)...");
  try {
    const res = await fetch("/api/v2/lesson", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, age, pdf_text }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || res.statusText);

    lastSyncLesson = data.lesson;
    // show plan preview & first tutor lines
    const preview = document.createElement("div");
    preview.className = "preview";
    const h = document.createElement("h3");
    h.textContent = data.lesson.title + " — " + data.lesson.duration;
    preview.appendChild(h);
    if (Array.isArray(data.lesson.objectives)) {
      const ul = document.createElement("ul");
      data.lesson.objectives.slice(0, 5).forEach((o) => {
        const li = document.createElement("li"); li.textContent = o; ul.appendChild(li);
      });
      preview.appendChild(ul);
    }
    if (Array.isArray(data.lesson.materials)) {
      const mHead = document.createElement("h4");
      mHead.textContent = "Matériel";
      preview.appendChild(mHead);
      const ml = document.createElement("ul");
      data.lesson.materials.forEach((m) => {
        const li = document.createElement("li"); li.textContent = m; ml.appendChild(li);
      });
      preview.appendChild(ml);
    }
    syncLessonEl.innerHTML = "";
    syncLessonEl.appendChild(preview);

      if (Array.isArray(data.lesson.ui_steps)) {
        renderUiSteps(syncLessonEl, data.lesson.ui_steps);
      }
      renderLessonCards(data.lesson);
      setStatus("✅ Leçon prête");
      $("#btnGenImgs").click();
    } catch (e) {
      setStatus("❌ " + e.message);
    }
});

/* =========================
   C) Generate images for the lesson
   ========================= */

$("#btnGenImgs").addEventListener("click", async () => {
  if (!lastSyncLesson || !Array.isArray(lastSyncLesson.image_prompts) || lastSyncLesson.image_prompts.length === 0) {
    setStatus("ℹ️ Pas d'images à générer (image_prompts manquant)");
    return;
  }
  setStatus("🎨 Génération d'images…");
  imagePreviewEl.innerHTML = "";
  imageTabsEl.innerHTML = "";
  selectedImage = null;
  try {
    const res = await fetch("/api/v2/generate_images", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_prompts: lastSyncLesson.image_prompts }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || res.statusText);

    (data.images || []).forEach((im, idx) => {
      if (im.data_url) {
        const btn = document.createElement("button");
        btn.className = "image-tab";
        const img = document.createElement("img");
        img.src = im.data_url;
        img.alt = im.id || "img";
        btn.appendChild(img);
        btn.onclick = () => {
          selectedImage = im.data_url;
          imagePreviewEl.innerHTML = "";
          const big = document.createElement("img");
          big.src = im.data_url;
          big.alt = im.id || "img";
          imagePreviewEl.appendChild(big);
          [...imageTabsEl.querySelectorAll(".image-tab")].forEach((t) => t.classList.remove("active"));
          btn.classList.add("active");
        };
        imageTabsEl.appendChild(btn);
        if (idx === 0) btn.click();
      } else if (im.error) {
        const err = document.createElement("div");
        err.className = "muted";
        err.textContent = `⚠️ ${im.id}: ${im.error}`;
        imageTabsEl.appendChild(err);
      }
    });
    lastSyncLesson.images = (data.images || []).map(im => ({...im, url: im.data_url}));
    renderLessonCards(lastSyncLesson);
    setStatus("✅ Images prêtes");
  } catch (e) {
    setStatus("❌ " + e.message);
  }
});

/* =========================
   D) Chat with Mimi
   ========================= */

$("#btnSend").addEventListener("click", async () => {
  const msg = $("#chatInput").value.trim();
  if (!msg) return;
  $("#chatInput").value = "";
  pushChat("user", msg);
  setStatus("💬 Mimi réfléchit…");

  try {
    const res = await fetch("/api/v2/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lesson: lastSyncLesson || {},
        history: chatHistory,
        message: msg,
      }),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
    const reply = data.reply || "🙂";
    chatHistory.push({ role: "user", content: msg });
    chatHistory.push({ role: "assistant", content: reply });
    pushChat("bot", reply, false);
    setStatus("✅");
  } catch (e) {
    setStatus("❌ " + e.message);
  }
});

$("#btnSpeak").addEventListener("click", () => {
  // speak last bot message
  for (let i = chatHistory.length - 1; i >= 0; i--) {
    if (chatHistory[i].role === "assistant") {
      speakText(chatHistory[i].content);
      break;
    }
  }
});
