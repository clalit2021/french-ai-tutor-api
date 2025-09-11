import React, { useState } from "react";
import { QuizCardRemote } from "./QuizCardRemote";

export function QuestionsFeed({ lessonId }) {
  const [idx, setIdx] = useState(0);
  const [score, setScore] = useState(0);

  return (
    <div style={{ padding: 20 }}>
      <h2 style={{ fontSize: 20, fontWeight: 800, marginBottom: 8 }}>Lesson</h2>
      <QuizCardRemote
        lessonId={lessonId}
        index={idx}
        onResult={({ correct }) => {
          if (correct) setScore((s) => s + 1);
        }}
      />
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
        <button
          type="button"
          onClick={() => setIdx((i) => Math.max(0, i - 1))}
          style={{ padding: "8px 12px", borderRadius: 10, border: "1px solid #e5e7eb", background: "#f9fafb" }}
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => setIdx((i) => i + 1)}
          style={{ padding: "8px 12px", borderRadius: 10, border: "1px solid #e5e7eb", background: "#f9fafb" }}
        >
          Next
        </button>
        <span style={{ marginLeft: 8 }}>Score: <strong>{score}</strong></span>
      </div>
    </div>
  );
}