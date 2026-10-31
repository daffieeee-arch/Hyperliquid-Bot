import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitRefreshProvider } from "../providers/cockpit-refresh";
import { AppShell } from "./app-shell";

const state = vi.hoisted(() => ({ pathname: "/paper", capture: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("./capture-pulse", () => ({
  CapturePulse: () => {
    state.capture();
    return <span>Legacy capture pulse</span>;
  },
}));

afterEach(() => {
  state.capture.mockClear();
  state.pathname = "/paper";
});

function render(nativePaperBound: boolean): string {
  return renderToStaticMarkup(
    <CockpitRefreshProvider>
      <AppShell nativePaperBound={nativePaperBound}>
        <div>page contents</div>
      </AppShell>
    </CockpitRefreshProvider>,
  );
}

describe("native PAPER shell isolation", () => {
  it("shows native branding and snapshot controls without constructing the capture poll", () => {
    const html = render(true);
    expect(html).toContain("BTC-PERP · NATIVE · PAPER");
    expect(html).toContain("Snapshot · liveness unverified");
    expect(html).toContain("Pause refresh");
    expect(html).toContain('aria-label="Refresh now"');
    expect(html).not.toContain("COURSE-1");
    expect(html).not.toContain("LIVE CAPTURE");
    expect(state.capture).not.toHaveBeenCalled();
  });

  it("retains the legacy shell on /paper when native binding is absent", () => {
    const html = render(false);
    expect(html).toContain("BTC-PERP · COURSE-1 · PAPER");
    expect(html).toContain("Legacy capture pulse");
    expect(state.capture).toHaveBeenCalledOnce();
  });

  it.each(["/", "/markets", "/research", "/system"])(
    "retains capture context on %s even when native PAPER is bound",
    (pathname) => {
      state.pathname = pathname;
      expect(render(true)).toContain("Legacy capture pulse");
      expect(state.capture).toHaveBeenCalledOnce();
    },
  );
});
