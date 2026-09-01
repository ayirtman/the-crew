import "@testing-library/jest-dom/vitest";

// jsdom has no media playback; stub it so components calling audio.play() cannot crash tests.
Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  writable: true,
  value: () => Promise.resolve(),
});
Object.defineProperty(HTMLMediaElement.prototype, "pause", {
  configurable: true,
  writable: true,
  value: () => undefined,
});
