"""Probe (M9, F7): how well the `embed` model's similarities separate a chat that answers a query
from the rest, with the query as typed (as search once sent it) and in the model's
own form (`api/search.py`'s `query_text`; for Qwen3-Embedding, the instruction its model card
asks for, "Instruct: {task}\\nQuery: {query}", documents without one). Chats are made up here, as chat_search stores them (question, blank line, answer); nothing is
written anywhere. Prints scores only. Run inside gen9-agent-api from the repo root:
docker exec -i gen9-agent-api-1 python - < gen9-agent/explore/search/relevance/probe.py"""

import asyncio
import math

import httpx

from gen9_agent.api.search import RELEVANT_SHARE, query_text
from gen9_agent.model_router import embed
from gen9_agent.settings import get_settings

CHATS = {
    "vacuum": "Why is autovacuum not keeping up on my orders table?\n\nRaise autovacuum_vacuum_scale_factor down to 0.02 for that table and give it more workers; dead tuples pile up because the default 20% threshold is too high for a large table.",
    "retries": "How do Temporal activities retry?\n\nEach activity has a retry policy: an initial interval, a backoff coefficient and a maximum number of attempts; non-retryable error types stop it.",
    "sourdough": "My sourdough starter smells like acetone, is it dead?\n\nNo, it's hungry. Feed it twice a day at 1:1:1 flour, water and starter and keep it warm.",
    "lighthouse": "Who kept the Eddystone lighthouse?\n\nKeepers lived in the tower in shifts of three; the fourth tower, Smeaton's, stood from 1759 until erosion of its rock in the 1870s.",
    "rfc": "What does RFC 10017 recommend for browser apps?\n\nA backend for frontend: the browser holds only a session cookie, and tokens stay on the server.",
    "passkeys": "How do I add a passkey to Keycloak?\n\nEnable the WebAuthn passwordless policy, then register a passkey from the account console or the required action.",
    "greeting": "Reply with one word: hi\n\nhi",
    "counting": "Count from 1 to 40, one number per line, nothing else.\n\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12",
    "lisbon": "Plan three days in Lisbon in May\n\nDay one Alfama and the castle, day two Belem and the tower, day three Sintra by train from Rossio.",
    "taxes": "When is the deadline for my self assessment tax return?\n\nOnline returns are due by 31 January after the tax year ends; paper ones by 31 October.",
    "asyncio": "Why does my asyncio program freeze when I call requests.get?\n\nrequests blocks the event loop; use httpx.AsyncClient or run it in asyncio.to_thread.",
    "kubernetes": "My pod keeps restarting with CrashLoopBackOff\n\nCheck kubectl logs --previous; the container exits because the readiness probe hits a port the app doesn't listen on yet.",
    "garden": "What should I plant in a shady corner of the garden?\n\nHostas, ferns and astilbe like shade; add leaf mould to the soil and water in dry spells.",
    "marathon": "How should I train for my first marathon in 16 weeks?\n\nBuild weekly distance by about 10 percent, one long run each week up to 32 km, and taper for the last two weeks.",
    "invoice": "Draft a polite reminder for an unpaid invoice\n\nHello Sam, a quick reminder that invoice 2031 for September is now 14 days overdue. Could you let me know when to expect payment?",
    "german": "Wie funktioniert die Mülltrennung in Berlin?\n\nPapier in die blaue Tonne, Verpackungen in die gelbe, Bioabfall in die braune, der Rest in die graue.",
    "ocr": "Extract the text from this scanned receipt\n\nCafe Nero, 2 flat whites 6.40, croissant 2.90, total 9.30, paid by card on 12 September.",
    "sql": "Write a query for the ten customers who spent the most last year\n\nselect customer_id, sum(amount) as spent from orders where created_at >= date '2025-01-01' and created_at < date '2026-01-01' group by 1 order by 2 desc limit 10;",
    "poem": "Write a short poem about autumn rain\n\nGrey threads unspool from a patient sky, the maples bow, the gutters sigh.",
    "hooks": "When should I use useEffect in React?\n\nFor synchronising with something outside React, such as a subscription; derive values during render instead.",
}
# Paraphrases that share few or no words with their chat
ANSWERED = {
    "database cleanup falling behind on a big table": "vacuum",
    "retrying failed background work": "retries",
    "bread yeast culture smells like nail polish": "sourdough",
    "lighthouses": "lighthouse",
    "numbers": "counting",
    "a trip to Portugal": "lisbon",
    "when do I have to file my taxes": "taxes",
    "event loop blocked by an HTTP call": "asyncio",
    "sign in without a password": "passkeys",
    "where should access tokens live in a single page app": "rfc",
    "Welche Türme warnen nachts Schiffe?": "lighthouse",
    "¿Cómo sé si mi masa madre sigue viva?": "sourdough",
    "Wohin kommt der Joghurtbecher?": "german",
    "container fails its health check and restarts": "kubernetes",
    "plants that grow without much sun": "garden",
    "long distance running plan": "marathon",
    "chasing a client who hasn't paid": "invoice",
    "what did I spend at the coffee shop": "ocr",
    "top spenders report": "sql",
    "verses about the fall season": "poem",
}
UNANSWERED = [
    "volcano eruptions",
    "knitting a scarf",
    "stock options vesting",
    "how tall is Mount Everest",
    "guitar chords for beginners",
    "Wie hoch ist die Zugspitze?",
    "recetas de paella",
    "black holes and event horizons",
    "how to tie a bow tie",
    "chess openings for black",
    "renewing a passport",
    "hi",
    "the",
    "test",
]


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


async def main() -> None:
    settings = get_settings()
    names = list(CHATS)
    queries = list(ANSWERED) + UNANSWERED
    async with httpx.AsyncClient(timeout=60) as http:
        docs, model = await embed(settings, http, [CHATS[n] for n in names])
        raw, _ = await embed(settings, http, queries)
        told, _ = await embed(settings, http, [query_text(q, model) for q in queries])
    print("model:", model)
    for label, vectors in (("raw query", raw), ("with instruction", told)):
        print(f"\n== {label}")
        right_first = 0
        answered_scores, other_best, unanswered_best, kept_wrong = [], [], [], 0
        for q, v in zip(queries, vectors, strict=True):
            scores = sorted(
                ((cos(v, d), n) for n, d in zip(names, docs, strict=True)), reverse=True
            )
            best = scores[0][0]
            kept = [n for s, n in scores if s >= RELEVANT_SHARE * best]
            want = ANSWERED.get(q)
            if want:
                mine = next(s for s, n in scores if n == want)
                others = max(s for s, n in scores if n != want)
                answered_scores.append(mine)
                other_best.append(others)
                right_first += scores[0][1] == want
                kept_wrong += len([n for n in kept if n != want])
                print(
                    f"  {q[:44]:44} right {mine:.3f}  best other {others:.3f}  kept {len(kept)}"
                )
            else:
                unanswered_best.append(best)
                kept_wrong += len(kept)
                print(
                    f"  {q[:44]:44} (none)       best {best:.3f} ({scores[0][1]})  kept {len(kept)}"
                )
        print(
            f"  right chat first: {right_first}/{len(ANSWERED)}; wrong chats kept by the 0.6 share: {kept_wrong}"
        )
        print(
            f"  right chat's score: min {min(answered_scores):.3f}; best wrong one: max {max(other_best):.3f}; with no right chat, best: max {max(unanswered_best):.3f}"
        )


asyncio.run(main())
