# Evaluating retrieval

## Metrics

Recall at k measures the fraction of relevant passages that appear in the top k results. Mean
reciprocal rank, or MRR, averages one over the rank of the first relevant result, so it rewards
putting a good passage first. Normalised discounted cumulative gain, nDCG, handles graded relevance
and discounts relevant results that appear lower in the ranking.

## Benchmarks

BEIR, introduced by Thakur and colleagues in 2021, is a benchmark of 18 datasets from different
domains, built to test retrievers on data they were not trained on. Its headline metric is nDCG at 10.
BEIR evaluated DPR alongside BM25 and found that DPR, despite its strong in-domain results, often fell
behind BM25 on unseen domains.

## Build your own

Public benchmarks rarely match a private collection. A few dozen questions written about your own
documents, each labelled with the passage that answers it, reveal more about a system than any
leaderboard.
