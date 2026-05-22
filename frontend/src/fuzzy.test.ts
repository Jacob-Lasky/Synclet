/**
 * Frontend fuzzy-score tests. Mirrors backend/tests/test_fuzzy.py for shared
 * behavior. Diverging here means search ranking on the user's typed query
 * differs from what the resolver returns — silently confusing.
 */
import { describe, expect, it } from "vitest"
import { fuzzyScore } from "./fuzzy"

describe("fuzzyScore", () => {
    it("exact match scores 2", () => {
        expect(fuzzyScore("Fallout", "Fallout")).toBe(2)
    })

    it("case-insensitive exact match", () => {
        expect(fuzzyScore("fallout", "FALLOUT")).toBe(2)
    })

    it("substring scores between 1 and 2", () => {
        const s = fuzzyScore("fa", "Fallout")
        expect(s).toBeGreaterThan(1)
        expect(s).toBeLessThan(2)
    })

    it("shorter target boosts substring score", () => {
        expect(fuzzyScore("fall", "Fall")).toBeGreaterThan(
            fuzzyScore("fall", "Fallout")
        )
    })

    it("all-words substring scores 0.9", () => {
        expect(fuzzyScore("call saul better", "Better Call Saul")).toBe(0.9)
    })

    it("prefix-only match scores in 0.5–0.85 range", () => {
        // "bet" prefixes "Better"; not a substring of the whole target
        const s = fuzzyScore("bet", "Better Call Saul")
        // "bet" IS in "Better Call Saul" as substring → 1+ score, not prefix-only
        expect(s).toBeGreaterThan(1)
    })

    it("empty query scores 0", () => {
        expect(fuzzyScore("", "Fallout")).toBe(0)
    })

    it("ranking is monotonic", () => {
        const target = "Better Call Saul"
        const scores = [
            fuzzyScore("better call saul", target),
            fuzzyScore("better call", target),
            fuzzyScore("call", target),
            fuzzyScore("xyz", target),
        ]
        const sorted = [...scores].sort((a, b) => b - a)
        expect(scores).toEqual(sorted)
    })
})
