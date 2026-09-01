"use client";

import { useState } from "react";

export default function Page() {
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<string>("");
  const [pending, setPending] = useState(false);

  async function submit() {
    setPending(true);
    try {
      const res = await fetch("/api/count", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      setResult(res.ok ? `${data.word_count} words` : data.error);
    } finally {
      setPending(false);
    }
  }

  return (
    <main>
      <input aria-label="url" value={url} onChange={(e) => setUrl(e.target.value)} />
      <button onClick={submit} disabled={pending}>
        Count
      </button>
      <p role="status">{result}</p>
    </main>
  );
}
