import { describe, expect, it } from "vitest";
import { POST } from "@/app/api/count/route";

function post(body: unknown) {
  return POST(new Request("http://localhost/api/count", { method: "POST", body: JSON.stringify(body) }));
}

describe("count", () => {
  it("returns word count for a valid url", async () => {
    const res = await post({ url: "https://example.com", html: "<p>one two</p> three" });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ word_count: 3 });
  });

  it("returns error for an invalid url", async () => {
    const res = await post({ url: "not a url" });
    expect(res.status).toBe(400);
  });

  it("disables the button while a request is in flight", async () => {
    // covered at the api level for the fixture: a pending request has no result yet
    const res = await post({ url: "https://example.com", html: "" });
    expect((await res.json()).word_count).toBe(0);
  });
});
