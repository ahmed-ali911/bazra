import { ImageOff } from "lucide-react";

export type LivingHeroScene =
  | "home"
  | "work"
  | "personal"
  | "learning"
  | "career"
  | "projects"
  | "ideas"
  | "teaching";

export type LivingHeroState =
  | "idle"
  | "working"
  | "thinking"
  | "stretching"
  | "celebrating"
  | "deadline"
  | "learning"
  | "teaching"
  | "serious";

export type LivingHeroMood = "neutral" | "focused" | "encouraging" | "playful" | "concerned" | "proud";

interface LivingHeroProps {
  scene?: LivingHeroScene;
  state: LivingHeroState;
  mood?: LivingHeroMood;
  className?: string;
}

// Stable API contract now, real character art later — this contract stays
// unchanged when a static placeholder image per state (or a real animation
// technology) eventually replaces the rendering below. No caller should
// ever need to know how a state is actually depicted.
//
// The placeholder is deliberately NOT art: a dashed border (every real
// component here uses a solid border/shadow), a generic ImageOff icon
// (signals "no art here" rather than risking any face/character shape that
// could be mistaken for a rough draft), and the three prop values rendered
// as plain text — which doubles as a way to visually confirm the right
// props are reaching the component during development.
export function LivingHero({ scene = "home", state, mood = "neutral", className = "" }: LivingHeroProps) {
  return (
    <div
      className={["rounded-lg bg-[var(--color-bg-accent-subtle)]", className].join(" ")}
      style={{
        width: 240,
        aspectRatio: "1",
        border: "2px dashed var(--color-border-divider)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-2)",
        textAlign: "center",
      }}
    >
      <ImageOff size={28} className="text-[var(--color-text-muted)]" aria-hidden="true" />
      <p className="text-[var(--color-text-heading)]">{state}</p>
      <p className="text-[var(--color-text-muted)] text-sm">
        scene: {scene}
        <br />
        mood: {mood}
      </p>
    </div>
  );
}
