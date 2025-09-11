import React, { useState } from "react";

// Core QuizCard component
export default function QuizCard({
  question,
  options,
  correctIndex = 0,
  onResult,
  shuffle = false,
}) {
  const [selected, setSelected] = useState(null);
  const [isCorrect, setIsCorrect] = useState(null);

  // Optionally shuffle options (keeps track of original index)
  const prepared = (function () {
    const withIndex = options.map((text, idx) => ({ text, idx }));
    if (!shuffle) return withIndex;
    return [...withIndex].sort(() => Math.random() - 0.5);
  })();

  const handleSelect = (opt) => {
    if (selected !== null) return; // prevent changing after answer
    setSelected(opt.idx);
    const ok = opt.idx === correctIndex;
    setIsCorrect(ok);
    onResult?.({ correct: ok, selectedIndex: opt.idx, correctIndex });
  };

  return (
    <div
      role="group"
      aria-labelledby="quiz-question"
      style={{
        width: "100%",
        maxWidth: 520,
        margin: "16px auto",
        padding: 16,
        borderRadius: 16,
        boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
        background: "white",
        border: "1px solid #eee",
        fontFamily:
          "system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif",
      }}
    >
      <div id="quiz-question" style={{ fontSize: 18, fontWeight: 700, marginBottom: 12 }}>
        {question}
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {prepared.map((opt) => {
          const pressed = selected === opt.idx;
          const base = {
            padding: "12px 14px",
            borderRadius: 12,
            border: "1px solid #e5e7eb",
            cursor: selected === null ? "pointer" : "default",
            fontSize: 16,
            textAlign: "left",
            transition: "transform 120ms ease, background 120ms ease, border-color 120ms ease",
            userSelect: "none",
            outline: "none",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          };

          let bg = "#fff";
          let br = "#e5e7eb";
          let transform = "none";
          let rightBadge = null;

          if (selected === null) {
            // no answer yet
          } else if (pressed) {
            if (isCorrect) {
              bg = "#ecfdf5"; // green-50
              br = "#10b981"; // green-500
              rightBadge = <span style={{ fontSize: 12, fontWeight: 700, color: "#065f46" }}>Correct ✓</span>;
            } else {
              bg = "#fef2f2"; // red-50
              br = "#ef4444"; // red-500
              rightBadge = <span style={{ fontSize: 12, fontWeight: 700, color: "#991b1b" }}>Try again ✗</span>;
            }
          } else if (selected !== null && opt.idx === correctIndex) {
            // Show the correct one when user picks wrong
            bg = "#f0fdf4";
            br = "#86efac";
          }

          return (
            <button
              key={opt.idx + "_" + opt.text}
              type="button"
              onClick={() => handleSelect(opt)}
              disabled={selected !== null}
              onMouseEnter={(e) => {
                if (selected === null) e.currentTarget.style.transform = "translateY(-1px)";
              }}
              onMouseLeave={(e) => {
                if (selected === null) e.currentTarget.style.transform = "none";
              }}
              aria-pressed={pressed}
              aria-label={`Answer option: ${opt.text}`}
              style={{ ...base, background: bg, borderColor: br, transform }}
            >
              <span>{opt.text}</span>
              {rightBadge}
            </button>
          );
        })}
      </div>

      {selected !== null && (
        <div style={{ marginTop: 12, fontSize: 14, color: isCorrect ? "#065f46" : "#7f1d1d" }}>
          {isCorrect ? "Great job!" : "That wasn't it. The correct answer is highlighted."}
        </div>
      )}
    </div>
  );
}