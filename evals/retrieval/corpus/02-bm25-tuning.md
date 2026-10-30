# Tuning BM25

## The two parameters

BM25 comes from the probabilistic relevance framework developed by Stephen Robertson and colleagues.
It has two parameters. The first, k1, controls term-frequency saturation: once a word has appeared a
few times, further occurrences add less and less to the score. Values between 1.2 and 2.0 are
common. The second, b, controls length normalisation, so that long documents are not favoured just
for containing more words. The standard value of b is 0.75.

## When to change them

Collections of short passages, such as chunks of a larger document, often do better with a lower b,
because the chunks are already of similar length. Very repetitive text, like transcripts of speech,
benefits from a lower k1, so that a word repeated many times does not dominate the ranking.

## Tokenisation matters more

In practice, how text is split into tokens changes results more than either parameter. Stemming,
stop-word removal, and handling of numbers and hyphenated words all decide which terms can match.
