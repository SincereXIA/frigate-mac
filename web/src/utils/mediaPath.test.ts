import { describe, expect, it } from "vitest";
import { mediaPathToUrlPath } from "./mediaPath";

describe("mediaPathToUrlPath", () => {
  it("converts the container media root", () => {
    expect(
      mediaPathToUrlPath("/media/frigate/clips/review/thumb-front.webp"),
    ).toBe("clips/review/thumb-front.webp");
  });

  it("converts a native macOS media root", () => {
    expect(
      mediaPathToUrlPath("/Volumes/camera/clips/review/thumb-front.webp"),
    ).toBe("clips/review/thumb-front.webp");
  });

  it("converts exports from a custom media root", () => {
    expect(mediaPathToUrlPath("/mnt/nvr/exports/front.mp4")).toBe(
      "exports/front.mp4",
    );
  });

  it("preserves an already relative recording path", () => {
    expect(mediaPathToUrlPath("recordings/2026-08-09/front.mp4")).toBe(
      "recordings/2026-08-09/front.mp4",
    );
  });
});
