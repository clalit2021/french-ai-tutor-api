<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>French AI Tutor – Démarrer une leçon</title>
  <style>
    :root { --bg:#f6f7fb; --card:#fff; --ink:#212529; --accent:#6c5ce7; --muted:#6b7280; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; color:var(--ink); background:linear-gradient(180deg,#eaeafe, #f9fbff);}
    .wrap { max-width:900px; margin:40px auto; padding:0 16px;}
    .title { font-size:28px; font-weight:700; margin-bottom:4px;}
    .sub { color:var(--muted); margin-bottom:20px;}
    .panel { background:var(--card); border-radius:16px; box-shadow:0 6px 24px rgba(0,0,0,.06); padding:18px; }
    .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
    .row > * { flex:1 1 auto; }
    input[type=text] { width:100%; padding:12px 14px; border:1px solid #e5e7eb; border-radius:12px; font-size:15px;}
    button { background:var(--accent); color:#fff; border:0; padding:12px 16px; border-radius:12px; font-weight:600; cursor:pointer; }
    button:disabled { opacity:.6; cursor:not-allowed; }
    .hint { font-size:13px; color:var(--muted); margin-top:6px;}
    .status { margin:14px 0; font-size:14px; color:var(--muted);}
    .steps { margin-top:18px; display:grid; gap:14px; }
    .card { background:#fff; border-radius:14px; padding:14px; border:1px solid #e6e9ff; }
    .card h4 { margin:0 0 6px 0; }
    .text-lg { font-size:16px; }
    .speak { font-weight:700; }
    .q-options { display:flex; flex-direction:column; gap:8px; margin-top:8px; }
    .q-options button { background:#f3f4ff; color:#111; border:1px solid #dfe3ff; }
    .correct { border-color:#22c55e !important; background:#dcfce7 !important; }
    .wrong { border-color:#ef4444 !important; background:#fee2e2 !important; }
    .img { width:100%; border-radius:12px; object-fit:cover; }
    .small { font-size:12px; color:#6b7280;}
    .idpill { display:inline-block; background:#eef; color:#334155; padding:3px 8px; border-radius:999px; font-size:12px; }
    code { background:#eef; padding:2px 6px; border-radius:6px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="title">Parle avec Mimi — Démarrer une leçon</div>
    <div class="sub">Entre le <b>chemin du fichier</b> dans Supabase Storage (ex: <code>uploads/Screenshot 2025-09-02 191857.png</code>) et clique <b>Démarrer</b>.</div>

    <div class="panel">
      <div class="row">
        <input id="filePath" type="text" placeholder="uploads/mon-fichier.pdf ou uploads/page.png" />
        <button id="startBtn">Démarrer la leçon</button>
      </div>
      <div class="hint">Enfant ID (démo): <span id="childId" class="idpill">2e735737-96fc-4d46-8bb9-19f72d8f6215</span></div>
      <div id="status" class="status"></div>
      <div id="steps" class="steps"></div>
    </div>

    <p class="small" style="margin-top:16px;">
      API: <code id="apiBaseShow"></code>
    </p>
  </div>

  <script>
    // ==== CONFIG ====
    const API_BASE = "https://french-ai-tutor-api.onrender.com"; // change if your API URL differs
    const CHILD_ID = "2e735737-96fc-4d46-8bb9-19f72d8f6215";     // your demo child_id
    document.getElementById('apiBaseShow').textContent = API_BASE;

    // ==== DOM refs ====
    const el = {
      path: document.getElementById('filePath'),
      start: document.getElementById('startBtn'),
      status: document.getElementById('status'),
      steps: document.getElementById('steps'),
    };

    function setStatus(msg){ el.status.textContent = msg; }
    function clearSteps(){ el.steps.innerHTML = ""; }

    // ==== helpers ====
    async function postJSON(url, body){
      const r = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }

    async function getJSON(url){
      const r = await fetch(url);
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }

    function speak(text, lang='fr-FR', rate=1.0){
      try {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = lang; u.rate = rate;
        window.speechSynthesis.speak(u);
      } catch(e) { console.warn('TTS failed', e); }
    }

    // ==== RENDERERS ====
    function renderLegacyStep(step){ // type: 'note'|'speak'|'question'|'image'
      const card = document.createElement('div');
      card.className = 'card';

      if(step.type === 'note' || step.type === 'text'){
        if(step.title){
          const h = document.createElement('h4'); h.textContent = step.title; card.appendChild(h);
        }
        if(step.text){
          const p = document.createElement('p'); p.textContent = step.text; card.appendChild(p);
          const b = document.createElement('button'); b.textContent = 'Écouter 🔊'; b.onclick = () => speak(step.text);
          card.appendChild(b);
        }
      } else if(step.type === 'speak'){
        const h = document.createElement('h4'); h.textContent = step.title || 'Répète après moi'; card.appendChild(h);
        const p = document.createElement('p'); p.textContent = step.text || ''; p.className = 'text-lg speak'; card.appendChild(p);
        const b = document.createElement('button'); b.textContent = 'Écouter & répéter 🔊'; b.onclick = () => speak(step.text || '');
        card.appendChild(b);
      } else if(step.type === 'question'){
        const h = document.createElement('h4'); h.textContent = step.prompt || step.question || 'Question'; card.appendChild(h);
        const box = document.createElement('div'); box.className = 'q-options';
        (step.options || []).forEach((opt, idx) => {
          const btn = document.createElement('button'); btn.textContent = opt;
          btn.onclick = () => {
            const correct = idx === (step.correct_option ?? step.answer_index);
            btn.classList.add(correct ? 'correct' : 'wrong');
          };
          box.appendChild(btn);
        });
        card.appendChild(box);
      } else if(step.type === 'image'){
        if(step.image_url){
          const img = document.createElement('img');
          img.className = 'img';
          img.src = step.image_url;
          img.alt = step.caption || 'image';
          card.appendChild(img);
        }
        if(step.caption){
          const p = document.createElement('p'); p.textContent = step.caption; card.appendChild(p);
        }
      } else {
        const p = document.createElement('p'); p.textContent = JSON.stringify(step); card.appendChild(p);
      }

      return card;
    }

    function renderPromptStep(step){ // format: { step: "...", prompt: "..." }
      const card = document.createElement('div');
      card.className = 'card';

      if(step.step){
        const p = document.createElement('p');
        p.className = 'text-lg';
        p.textContent = "👉 " + step.step;
        card.appendChild(p);
      }
      if(step.prompt){
        const q = document.createElement('p');
        q.className = 'text-lg speak';
        q.textContent = "🗣️ " + step.prompt;
        card.appendChild(q);

        const b = document.createElement('button');
        b.textContent = 'Écouter 🔊';
        b.onclick = () => speak(step.prompt);
        card.appendChild(b);
      }

      // if it accidentally includes image_url/caption, show them
      if(step.image_url){
        const img = document.createElement('img');
        img.className = 'img';
        img.src = step.image_url;
        img.alt = step.caption || 'image';
        card.appendChild(img);
      }
      if(step.caption){
        const c = document.createElement('p');
        c.textContent = step.caption;
        card.appendChild(c);
      }

      return card;
    }

    function renderStepAuto(step){
      // choose renderer based on fields present
      if (step && (step.type || step.options || step.image_url)) {
        return renderLegacyStep(step);
      }
      if (step && (step.step || step.prompt)) {
        return renderPromptStep(step);
      }
      // fallback
      const card = document.createElement('div');
      card.className = 'card';
      const p = document.createElement('p'); p.textContent = JSON.stringify(step);
      card.appendChild(p);
      return card;
    }

    // ==== FLOW ====
    async function pollLesson(lessonId){
      setStatus(`⏳ En cours… (lesson_id: ${lessonId})`);
      clearSteps();

      const started = Date.now();
      const TIMEOUT_MS = 120000; // 2 minutes
      while (Date.now() - started < TIMEOUT_MS){
        await new Promise(r => setTimeout(r, 1500));
        try {
          const data = await getJSON(`${API_BASE}/api/lessons/${lessonId}`);
          if (data.status === 'completed'){
            setStatus('✅ Terminé');
            clearSteps();

            // steps could be in data.lesson.ui_steps OR data.lesson.lesson_data.ui_steps
            const steps =
              (data.lesson && data.lesson.ui_steps) ||
              (data.lesson && data.lesson.lesson_data && data.lesson.lesson_data.ui_steps) ||
              [];

            steps.forEach(s => el.steps.appendChild(renderStepAuto(s)));
            return;
          }
          if (data.status === 'error'){
            setStatus('❌ Erreur pendant la génération.');
            return;
          }
        } catch(e) {
          console.warn('poll failed', e);
        }
      }
      setStatus('⌛ Temps dépassé.');
    }

    async function startLesson(){
      const path = el.path.value.trim();
      if(!path){ alert("Entre un chemin de fichier valide"); return; }

      el.start.disabled = true;
      setStatus("Envoi…");
      clearSteps();

      try {
        const resp = await postJSON(`${API_BASE}/api/lessons`, {
          child_id: CHILD_ID,
          file_path: path
        });
        if (resp.lesson_id){
          pollLesson(resp.lesson_id);
        } else {
          setStatus("❌ Erreur: pas de lesson_id");
        }
      } catch(e){
        setStatus(`❌ Erreur API: ${e.message}`);
      } finally {
        el.start.disabled = false;
      }
    }

    el.start.onclick = startLesson;
  </script>
</body>
</html>
