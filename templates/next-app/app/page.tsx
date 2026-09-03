"use client";
// Kit showcase. Builders: delete this page and write the real one — but build it like this.
import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { Card } from "@/components/Card";
import { BigButton } from "@/components/BigButton";
import { ImageTile } from "@/components/ImageTile";
import { ResultBanner } from "@/components/ResultBanner";
import { ProgressDots } from "@/components/ProgressDots";
import { LanguagePicker } from "@/components/LanguagePicker";
import { TimerRing } from "@/components/TimerRing";
import { StatPanel } from "@/components/StatPanel";

export default function Home() {
  const [lang, setLang] = useState("en");
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <AppShell palette="sky" title="Kit Showcase">
      <Card>
        <div className="flex items-center justify-between gap-4">
          <ProgressDots value={2} total={5} />
          <TimerRing remaining={42} total={60} />
        </div>
      </Card>
      <Card>
        <div className="flex gap-4">
          <ImageTile src="/assets/images/dog.svg" alt="dog" label="Dog"
            state={picked === "dog" ? "correct" : "idle"} onSelect={() => setPicked("dog")} />
          <ImageTile src="/assets/images/cat.svg" alt="cat" label="Cat"
            state={picked === "cat" ? "wrong" : "idle"} onSelect={() => setPicked("cat")} />
        </div>
      </Card>
      {picked ? (
        <ResultBanner variant={picked === "dog" ? "success" : "error"}>
          {picked === "dog" ? "Yes! That is the dog 🎉" : "That was the cat — try again"}
        </ResultBanner>
      ) : null}
      <Card>
        <LanguagePicker languages={["en", "de", "tr", "es", "fr"]} selected={lang} onSelect={setLang} />
      </Card>
      <Card>
        <StatPanel stats={[{ label: "Accuracy", value: "82%" }, { label: "Words", value: "14" }, { label: "Sessions", value: "6" }]} />
      </Card>
      <BigButton full onClick={() => setPicked(null)}>Play again</BigButton>
    </AppShell>
  );
}
