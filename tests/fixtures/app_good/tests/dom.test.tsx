import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Page from "@/app/page";

describe("page", () => {
  it("renders the count button", () => {
    render(<Page />);
    expect(screen.getByRole("button", { name: "Count" })).toBeInTheDocument();
  });
});
