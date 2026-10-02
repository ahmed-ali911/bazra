import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatMessageBubble } from "./ChatMessageBubble";
import type { ChatMessage } from "./types";

function assistantMessage(content: string): ChatMessage {
  return { id: 1, role: "assistant", content, created_at: "2026-10-02T16:27:00Z" };
}

function userMessage(content: string): ChatMessage {
  return { id: 2, role: "user", content, created_at: "2026-10-02T16:27:00Z" };
}

describe("ChatMessageBubble — bidi", () => {
  it("gives an Arabic-only message a dir='auto' content block", () => {
    const { container } = render(<ChatMessageBubble message={assistantMessage("عندك مهمة متأخرة من امبارح.")} />);
    const content = container.querySelector("[dir='auto']");
    expect(content).toBeInTheDocument();
    expect(content).toHaveTextContent("عندك مهمة متأخرة من امبارح.");
  });

  it("gives an English-only message a dir='auto' content block", () => {
    const { container } = render(<ChatMessageBubble message={assistantMessage("Your task is overdue.")} />);
    expect(container.querySelector("[dir='auto']")).toHaveTextContent("Your task is overdue.");
  });

  it("does not force a mixed Arabic+English message into a single wrong-direction block — the whole sentence stays in one dir='auto' paragraph for the browser's own bidi algorithm to resolve", () => {
    const { container } = render(
      <ChatMessageBubble message={assistantMessage("عندك اجتماع Project Review الساعة 10:00 صباحًا.")} />,
    );
    const paragraph = container.querySelector("p[dir='auto']");
    expect(paragraph).toHaveTextContent("Project Review");
    expect(paragraph).toHaveTextContent("عندك اجتماع");
  });

  it("isolates inline code/identifiers as dir='ltr' with unicode-bidi isolation", () => {
    const { container } = render(assistantBubble("راجع `RCIP-6` قبل الساعة."));
    const code = container.querySelector("code");
    expect(code).toHaveAttribute("dir", "ltr");
    expect(code).toHaveTextContent("RCIP-6");
    expect(code?.style.unicodeBidi).toBe("isolate");
  });

  it("lets each list item resolve its own direction independently", () => {
    const content = "عندك 3 حاجات:\n\n- **أكلم حسين**\n- **Project Review**\n- `RCIP-6`";
    const { container } = render(assistantBubble(content));
    const items = container.querySelectorAll("li");
    expect(items.length).toBe(3);
    for (const item of items) {
      expect(item).toHaveAttribute("dir", "auto");
    }
  });

  it("preserves faithful bidi-capable rendering for user messages too, without Markdown transformation", () => {
    const { container } = render(<ChatMessageBubble message={userMessage("مرحبا hello مرحبا")} />);
    const paragraph = container.querySelector("p[dir='auto']");
    expect(paragraph).toHaveTextContent("مرحبا hello مرحبا");
  });
});

function assistantBubble(content: string) {
  return <ChatMessageBubble message={assistantMessage(content)} />;
}

describe("ChatMessageBubble — Markdown", () => {
  it("renders bold text as a real <strong>, never literal **", () => {
    render(assistantBubble("هاي **نص عريض** تمام"));
    expect(screen.getByText("نص عريض").tagName).toBe("STRONG");
    expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
  });

  it("renders italics as a real <em>", () => {
    render(assistantBubble("this is *italic* text"));
    expect(screen.getByText("italic").tagName).toBe("EM");
  });

  it("renders an unordered list as real <ul>/<li> elements, not decorative bullet strings", () => {
    const content = "عندك 3 حاجات:\n\n- **أكلم حسين**\n- **Project Review**\n- `RCIP-6`";
    const { container } = render(assistantBubble(content));
    expect(container.querySelector("ul")).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(screen.queryByText(/^•/)).not.toBeInTheDocument();
  });

  it("renders inline code as a real <code> element", () => {
    render(assistantBubble("راجع `RCIP-6` دلوقتي"));
    const code = screen.getByText("RCIP-6");
    expect(code.tagName).toBe("CODE");
  });

  it("renders two newline-separated paragraphs as two separate <p> blocks", () => {
    const { container } = render(assistantBubble("أول نقطة مهمة.\n\nتاني نقطة هنا."));
    const paragraphs = container.querySelectorAll("p");
    expect(paragraphs.length).toBeGreaterThanOrEqual(2);
    expect(container).toHaveTextContent("أول نقطة مهمة.");
    expect(container).toHaveTextContent("تاني نقطة هنا.");
  });

  it("does not apply Markdown parsing to user-authored text (literal ** stays visible, faithfully preserved)", () => {
    render(<ChatMessageBubble message={userMessage("I like **this**")} />);
    expect(screen.getByText(/I like \*\*this\*\*/)).toBeInTheDocument();
  });
});

describe("ChatMessageBubble — security", () => {
  it("never executes a <script> tag embedded in assistant content", () => {
    const { container } = render(assistantBubble("<script>window.__xss = true;</script>"));
    expect((window as unknown as { __xss?: boolean }).__xss).toBeUndefined();
    expect(container.querySelector("script")).not.toBeInTheDocument();
  });

  it("never renders an onerror image handler as live HTML", () => {
    const { container } = render(assistantBubble('<img src=x onerror="window.__xss2 = true">'));
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect((window as unknown as { __xss2?: boolean }).__xss2).toBeUndefined();
  });

  it("never renders a javascript: link as a clickable anchor", () => {
    const { container } = render(assistantBubble('<a href="javascript:alert(1)">click</a>'));
    const anchor = container.querySelector("a");
    expect(anchor === null || anchor.getAttribute("href") !== "javascript:alert(1)").toBe(true);
  });

  it("never executes raw HTML embedded in a USER message either", () => {
    const { container } = render(<ChatMessageBubble message={userMessage("<script>window.__xss3 = true;</script>")} />);
    expect((window as unknown as { __xss3?: boolean }).__xss3).toBeUndefined();
    expect(container.querySelector("script")).not.toBeInTheDocument();
  });
});

describe("ChatMessageBubble — timestamp", () => {
  it("renders a <time> element sourced from created_at, not an ISO timestamp", () => {
    const { container } = render(assistantBubble("hello"));
    const time = container.querySelector("time");
    expect(time).toBeInTheDocument();
    expect(time).toHaveAttribute("datetime", "2026-10-02T16:27:00Z");
    expect(time?.textContent).not.toContain("T16:27");
  });
});

describe("ChatMessageBubble — bubble placement vs content direction", () => {
  it("keeps a user bubble aligned to the user side even when its text is Arabic", () => {
    const { container } = render(<ChatMessageBubble message={userMessage("تمام، شكرا")} />);
    const bubble = container.querySelector("[role='listitem']") as HTMLElement;
    expect(bubble.style.alignSelf).toBe("flex-end");
  });

  it("keeps an assistant bubble aligned to the assistant side even when its text is English", () => {
    const { container } = render(<ChatMessageBubble message={assistantMessage("Your task is overdue.")} />);
    const bubble = container.querySelector("[role='listitem']") as HTMLElement;
    expect(bubble.style.alignSelf).toBe("flex-start");
  });
});

describe("ChatMessageBubble — mobile/overflow", () => {
  it("allows long unbroken tokens to wrap instead of exploding the bubble width", () => {
    const { container } = render(assistantBubble("`RCIP-0000000000000000000000000000000000000001`"));
    const bubble = container.querySelector("[role='listitem']") as HTMLElement;
    expect(bubble.style.overflowWrap).toBe("anywhere");
  });
});
