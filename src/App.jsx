import React from "react";
import { QuestionsFeed } from "./components/QuestionsFeed";

export default function App() {
  // Replace with your real lesson id
  const lessonId = "55ab2366-ec6f-4847-b63d-bfbc4cc43012";
  return <QuestionsFeed lessonId={lessonId} />;
}