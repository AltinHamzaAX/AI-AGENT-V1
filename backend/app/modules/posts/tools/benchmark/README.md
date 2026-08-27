# Professional benchmark protocol

The versioned JSON catalog defines reproducible briefs and review rubrics. A URL is visual
context for the human reviewer, never permission to copy brand identity, layouts, or copy.

## Review procedure

1. Generate a post from the benchmark brief and declared assets without changing its constraints.
2. Review the final exported render at native size and at mobile-feed size.
3. A designer scores visual craft dimensions; a marketing expert or creative director scores
   strategy, audience fit, message, and conversion behavior.
4. Submit one overall human score plus evidence-bearing dimension feedback. The API binds that
   judgement to the server-owned AI quality report and exact render checksum.
5. Do not negotiate the human score after seeing the AI score. The signed difference is always
   `AI - human`; positive bias means the system overrates itself.

## Calibration policy

A profile stays `insufficient_data` until it has the configured minimum number of reviews and at
least two expert disciplines. Ready profiles report mean bias, MAE, RMSE, correlation when
variance permits, recurring weak dimensions, and dimension offsets. Calibration artifacts are
version-specific and auditable; they do not silently rewrite historical scores or bypass hard
verification gates.
