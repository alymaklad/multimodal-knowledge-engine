# Hybrid retrieval: a short survey

## Why combine retrievers

Lexical retrieval with BM25 matches the exact words of a query, which makes it reliable for names,
codes, and rare technical terms. Dense retrieval embeds queries and passages as vectors and matches
them by meaning, so it finds passages that use different words for the same idea. Each method fails
where the other succeeds, which is why most modern search systems run both and merge the results.

## Reciprocal rank fusion

Reciprocal rank fusion (RRF), introduced by Cormack, Clarke and Buettcher in 2009, merges several
ranked lists by summing one over the rank of each document, offset by a constant k. The usual value
of k is 60. Because RRF uses only positions in each list, it needs no score normalisation, so it
stays robust when BM25 scores and cosine similarities sit on completely different scales.

## Approximate nearest neighbours

Dense search over millions of vectors cannot compare the query with every vector. Approximate
nearest neighbour indexes trade a little recall for a large speed-up. The most widely used is HNSW,
a hierarchical navigable small world graph proposed by Malkov and Yashunin, which searches from
coarse upper layers down to a dense bottom layer.
