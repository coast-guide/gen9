"use client";

import { Cancel01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Form from "next/form";
import { useRef, useState } from "react";

import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import type { SearchMode } from "@/lib/agent";
import { MODES } from "@/lib/search";

/**
 * The query and the mode, kept in the URL (/search?q=…&mode=…) so Back and reload return to the
 * same results. It searches on Enter and when the mode changes, never per keystroke: "All" and
 * "Meaning" embed the query through the model router (docs/design/screens/search.md).
 */
export function SearchForm({ q, mode }: { q: string; mode: SearchMode }) {
  const form = useRef<HTMLFormElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(q);

  return (
    <Form ref={form} action="/search" role="search" className="grid gap-3">
      <label htmlFor="search-q" className="sr-only">
        Search your chats
      </label>
      <InputGroup className="h-11 bg-card">
        <InputGroupAddon>
          <HugeiconsIcon icon={Search01Icon} strokeWidth={1.8} className="size-[18px]" />
        </InputGroupAddon>
        <InputGroupInput
          ref={input}
          id="search-q"
          name="q"
          type="search"
          value={text}
          onChange={(event) => setText(event.target.value)}
          // Only when there's nothing to show yet, so a results page doesn't jump to the field
          autoFocus={!q}
          maxLength={500}
          enterKeyHint="search"
          placeholder="Search your chats"
          className="text-base [&::-webkit-search-cancel-button]:appearance-none"
        />
        {text && (
          <InputGroupAddon align="inline-end">
            <InputGroupButton
              size="icon-sm"
              aria-label="Clear"
              className="pointer-coarse:size-11"
              onClick={() => {
                setText("");
                input.current?.focus();
              }}
            >
              <HugeiconsIcon icon={Cancel01Icon} strokeWidth={2} />
            </InputGroupButton>
          </InputGroupAddon>
        )}
      </InputGroup>
      <fieldset className="flex flex-wrap gap-1.5">
        <legend className="sr-only">Find chats by</legend>
        {MODES.map((option) => (
          <label key={option.value} className="relative">
            <input
              type="radio"
              name="mode"
              value={option.value}
              defaultChecked={mode === option.value}
              className="peer sr-only"
              onChange={() => {
                if (text.trim()) form.current?.requestSubmit();
              }}
            />
            <span
              className={
                "inline-flex min-h-9 cursor-pointer items-center rounded-full border px-4 text-sm font-medium text-muted-foreground transition-colors pointer-coarse:min-h-11 " +
                "hover:text-foreground peer-checked:border-primary peer-checked:bg-primary peer-checked:text-primary-foreground " +
                "peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50"
              }
            >
              {option.label}
            </span>
          </label>
        ))}
      </fieldset>
    </Form>
  );
}
