import { NextResponse } from "next/server";
import { countWords, isValidUrl } from "@/lib/count";

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { url?: string; html?: string };
  const url = body.url ?? "";
  if (!isValidUrl(url)) {
    return NextResponse.json({ error: "invalid url" }, { status: 400 });
  }
  const html = body.html ?? "";
  return NextResponse.json({ word_count: countWords(html), title: url });
}
