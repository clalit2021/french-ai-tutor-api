import React, { useEffect, useMemo, useState, useCallback } from "react";
import QuizCard from "./QuizCard";

const API_BASE =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE) ||
  (typeof process !== "undefined" && process.env?.REACT_APP_API_BASE) ||
  ""; // same-origin fallback

/**
 * Props
 * - lessonId (string)        — required
 * - index (number)           — which question to fetch (0-based)
 * - onLoaded(q)              — optional callback when a question loads
 * - onResult(r)              — optional callback after user answers
 * - endpoints (optional)     — override builder for URLs { getQuestion(lessonId, index), submitAnswer() }
 */
export function QuizCardRemote({
  lessonId,
  index = 0,
  onLoaded,
  onResult,
  endpoints,
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState(null);

  const urlBuilder = useMemo(() => {
    if (endpoints) return endpoints;
    return {
      getQuestion: (lessonId, i) =>
        `${API_BASE}/api/lessons/${encodeURIComponent(lessonId)}/questions?limit=1&offset=${i}`,
      submitAnswer: () => `${API_BASE}/api/answers`,
    };
  }, [endpoints]);

  useEffect(() => {
    let aborted = false;
    async function run() {
      setLoading(true);
      setError("");
      try {
        const res = await fetch(urlBuilder.getQuestion(lessonId, index));
        if (!res.ok) throw new Error(`GET failed ${res.status}`);
        const data = await res.json();
        const item = Array.isArray(data?.items) ? data.items[0] : data; // supports array wrapper or single object
        if (!item) throw new Error("No question returned");
        if (!aborted) {
          setQ(item);
          onLoaded?.(item);
        }
      } catch (e) {
        if (!aborted) setError(e.message || String(e));
      } finally {
        if (!aborted) setLoading(false);
      }
    }
    run();
    return () => {
      aborted = true;
    };
  }, [lessonId, index, urlBuilder, onLoaded]);

  const handleResult = useCallback(
    async ({ correct, selectedIndex }) => {
      onResult?.({ correct, selectedIndex, correctIndex: q?.correctIndex, questionId: q?.id });
      try {
        await fetch(urlBuilder.submitAnswer(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            lessonId,
            questionId: q?.id,
            selectedIndex,
            correct,
          }),
        });
      } catch (e) {
        console.warn("submit answer failed:", e);
      }
    },
    [onResult, urlBuilder, lessonId, q]
  );

  if (loading) return <div style={{ padding: 16 }}>Loading…</div>;
  if (error) return <div style={{ padding: 16, color: "#b91c1c" }}>Error: {error}</div>;
  if (!q) return <div style={{ padding: 16 }}>No data.</div>;

  return (
    <QuizCard
      question={q.question}
      options={q.options}
      correctIndex={q.correctIndex}
      onResult={handleResult}
    />
  );
}