export function countWords(text: string): number {
  const stripped = text.replace(/<[^>]+>/g, " ");
  return stripped.split(/\s+/).filter((w) => w.length > 0).length;
}

export function isValidUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}
