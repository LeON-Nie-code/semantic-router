#!/usr/bin/env python3
"""Build the fixed FactCheck training comparison from Router Signal Suite."""

import argparse
import json
from pathlib import Path

NEGATIVE = "NO_FACT_CHECK_NEEDED"
POSITIVE = "FACT_CHECK_NEEDED"
RELEASE = "vllm-sr/vela3-fast"
RELEASE_REVISION = "7cbefd4b92b57234bd0ffb9872451e81abcdac79"

# Review the request and the available evidence, including whether a cited
# document is supplied in the prompt or must be fetched separately.
CORRECTIONS = {
    "fact_check-train-001047": NEGATIVE,  # Count a letter in the supplied word.
    "fact_check-train-001744": NEGATIVE,  # Add the supplied numbers.
    "fact_check-train-002046": NEGATIVE,  # Simplify the supplied text.
    "fact_check-train-001888": POSITIVE,  # Organize information from a linked page.
    "fact_check-train-004319": POSITIVE,  # Summarize a linked page.
    "fact_check-train-004889": POSITIVE,  # Summarize a linked article.
}

EVIDENCE_CASES = (
    (
        "When does Bristol Central Library close today?",
        "Today's library hours: 9 AM to 6 PM.",
        "The library has a quiet room.",
    ),
    (
        "What is the current adult admission price at Boston's Museum of Science?",
        "Current adult admission is 12 dollars.",
        "The museum has two floors.",
    ),
    (
        "Which platform is tonight's 19:17 train from York to Leeds using at York station?",
        "Tonight's 19:17 departure uses platform 4.",
        "The station has a seating area.",
    ),
    (
        "Is Dublin's Markievicz Leisure Centre open on this Monday's holiday?",
        "This Monday the leisure centre will be closed for the holiday.",
        "The pool has a shallow lane.",
    ),
    (
        "How much notice does the latest Bristol City Hall rental policy require for cancellations?",
        "The latest hall policy requires 48 hours' notice for cancellations.",
        "The hall has a stage.",
    ),
    (
        "Which team won yesterday's Maryland District 7 school baseball final?",
        "Yesterday's district final was won by the Orioles.",
        "Both teams wore blue.",
    ),
    (
        "What percentage was reported in the latest Edinburgh Resident Survey's first table?",
        "The new survey's first table reports 36%.",
        "The survey has a glossary.",
    ),
    (
        "Which kit version does this morning's Raspberry Pi SDK release notice list?",
        "This morning's release notice lists kit version 3.4.",
        "Release notices have a standard header.",
    ),
    (
        "Which hiking trail is closed according to today's Yellowstone trail alert?",
        "Today's park alert closes the Heron Trail.",
        "The park has a picnic area.",
    ),
    (
        "When is this year's Toronto Parks winter program registration deadline?",
        "This year's winter registration closes December 3.",
        "Winter registration uses an online form.",
    ),
    (
        "How much does IKEA's latest UK listing for the BRIMNES daybed charge for shipping?",
        "The latest listing charges £8 for shipping.",
        "The listing has a product photo.",
    ),
    (
        "Is Seattle Saturday compost collection cancelled this week?",
        "This week Saturday compost collection is cancelled.",
        "Compost is collected in green bins.",
    ),
    (
        "What room does the updated PyCon US workshop schedule give for the afternoon packaging lab?",
        "The updated schedule puts the afternoon lab in room B12.",
        "The workshops have a lunch break.",
    ),
    (
        "Who handles submissions according to the latest SIGIR call for short papers?",
        "The newest call assigns short-paper submissions to the program chair.",
        "The call accepts PDF documents.",
    ),
    (
        "What is the current transfer limit in the latest Wise UK personal account terms for domestic GBP transfers?",
        "The latest terms cap domestic GBP transfers at £250 per transfer.",
        "The service sends receipts.",
    ),
    (
        "Where will the next Boston City Council public budget hearing take place?",
        "The next public hearing is at City Hall in room 3.",
        "The hearing will have a public comment period.",
    ),
)

CLARIFICATIONS = (
    "Rewrite the email I sent to the landlord to sound friendlier.",
    "Which of my two drafts did I agree to send?",
    "Summarize the attachment I forgot to upload.",
    "What is the subtotal on my receipt? I don't have the receipt with me.",
    "Translate the second sentence of my last text message into Spanish.",
    "Can you explain why my meeting was moved? I haven't shared the invitation.",
    "Compare the two photos on my phone and pick the clearer one.",
    "Calculate the area of my desk. What measurements do you need?",
)

ARITHMETIC_CASES = (
    (
        "A theatre sells 14 tickets at 22 credits each. How many credits does it collect?",
        "14 × 22",
    ),
    (
        "A room is 3.7 metres above street level and another is 1.9 metres below it. What is their vertical separation?",
        "3.7 - (-1.9)",
    ),
    (
        "A 240-credit jacket is discounted by 15%. What is the sale price?",
        "240 × (1 - 0.15)",
    ),
    (
        "A class splits 59 counters between 8 pupils evenly. How many are left over?",
        "59 mod 8",
    ),
    (
        "Two stops took 12 minutes each and the connecting walk took 17. What was the total?",
        "2 × 12 + 17",
    ),
    (
        "A sensor reports 11, 10, and 6 at equal intervals. What is the mean reading?",
        "(11 + 10 + 6) / 3",
    ),
    (
        "Three quarters of the 20 seats are booked. How many seats are still free?",
        "20 × (1 - 3/4)",
    ),
    ("A square plot has sides of 13 metres. What is its area?", "13²"),
    (
        "A 600-metre walkway is drawn as 5 centimetres. How many metres does one centimetre represent?",
        "600 / 5",
    ),
    (
        "A fund starts with 100 credits and is multiplied by 1.1 each year. What is the amount after two years?",
        "100 × 1.1²",
    ),
    (
        "An account has 14 credits, pays 21, and then receives 8. What is the final balance?",
        "14 - 21 + 8",
    ),
    (
        "A rectangle measures 7 by 23 centimetres. What length of tape covers its perimeter?",
        "2 × (7 + 23)",
    ),
)


def suite_rows(suite, split):
    return [
        json.loads(line)
        for path in sorted(suite.glob(f"*/fact_check/{split}.jsonl"))
        for line in path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]


def hard_negatives():
    for n in range(16):
        start, end = 18 + n, 45 + n * 2
        yield f"A crate held {end} bottles. {start} were removed for the shop. How many remain?"
        yield f"Each box contains {start} tiles. How many tiles fit in {3 + n % 5} full boxes?"
        yield f"The form says delivery is free above {end} credits. This order is {start}. Is it free?"
        yield f"Stock log: Monday {start}; Tuesday {end}. By how many units did stock rise?"
        yield f"Edit for brevity: 'The branch processed a total of {end} packages over the course of the day.'"
        yield f"Use these premises only. Cases with at least {end} points pass. Case X has {start}. Does X pass?"


def revised_train(rows):
    additions = [
        {"id": f"authored-negative-{i:03d}", "text": text, "label": NEGATIVE}
        for i, text in enumerate(hard_negatives())
    ]
    eligible = {
        i
        for i, row in enumerate(rows)
        if row["label"] == NEGATIVE and row["id"] not in CORRECTIONS
    }
    output = [
        {**row, "label": CORRECTIONS.get(row["id"], row["label"])} for row in rows
    ]
    for row in additions:
        match = min(
            eligible, key=lambda i: (abs(len(rows[i]["text"]) - len(row["text"])), i)
        )
        eligible.remove(match)
        output[match] = row
    return output


def controlled_rows():
    for pair, (question, evidence, distractor) in enumerate(EVIDENCE_CASES):
        for label, context in ((NEGATIVE, evidence), (POSITIVE, distractor)):
            yield (
                "evidence",
                pair,
                label,
                f"Use the excerpt for any relevant facts it contains.\n{context}\n\n{question}",
            )
    for pair, (story, expression) in enumerate(ARITHMETIC_CASES):
        yield ("arithmetic", pair, NEGATIVE, story)
        yield ("arithmetic", pair, NEGATIVE, f"Evaluate {expression}.")
    for i, text in enumerate(CLARIFICATIONS):
        yield ("clarify", i, NEGATIVE, text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", type=Path, required=True, help="Router Signal Suite text directory"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    stock = suite_rows(args.suite, "train")
    if len(stock) != 5252 or not CORRECTIONS.keys() <= {row["id"] for row in stock}:
        parser.error(
            "expected the published FactCheck training split at revision fa08b2a6"
        )
    args.out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("stock", stock), ("revised", revised_train(stock))):
        with (args.out / f"{name}.jsonl").open("w", encoding="utf-8") as dest:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False), file=dest)


if __name__ == "__main__":
    main()
