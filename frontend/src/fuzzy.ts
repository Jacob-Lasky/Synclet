/**
 * Fuzzy title scoring — port of synclet/fuzzy.py:fuzzy_score on the backend.
 *
 * The frontend search bar and the backend resolver both use this algorithm;
 * keep them in sync. Whenever a tuning constant or branch changes here, the
 * matching change goes in the Python file, and vice versa.
 */
export function fuzzyScore(query: string, target: string): number {
    if (!query) return 0
    const q = query.toLowerCase()
    const t = target.toLowerCase()
    if (q === t) return 2
    if (t.includes(q)) return 1 + q.length / Math.max(t.length, 1)
    const words = q.split(/\s+/).filter(Boolean)
    if (words.length && words.every((w) => t.includes(w))) return 0.9
    const tWords = t.split(/\s+/)
    let prefix = 0
    for (const w of words) {
        if (tWords.some((tw) => tw.startsWith(w))) prefix++
    }
    if (prefix > 0) return 0.5 + (prefix / words.length) * 0.35
    // SequenceMatcher-style ratio — not portable from Python, but the fallback
    // tier just needs to be small enough to fall below resolve.py's _FUZZY_MIN_SCORE.
    // For the frontend we use the same threshold (0.1) the existing filter
    // applied, so this approximation is fine.
    return 0
}
