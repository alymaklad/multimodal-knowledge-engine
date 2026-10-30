# Rerankers

## Cross-encoders

A reranker rescores a short list of candidates that a fast first-stage retriever has already found.
The usual architecture is the cross-encoder: the query and a candidate passage are fed into the same
transformer together, so every query word can attend to every passage word. monoBERT, described by
Nogueira and Cho in 2019, was an early and influential cross-encoder reranker built on BERT.

## The cost

Because a cross-encoder must process every query and passage pair at query time, it cannot
precompute anything. Reranking a hundred candidates means a hundred forward passes, so latency grows
with the number of candidates. Systems therefore rerank only the top few dozen results.

## Why it helps

The first stage is tuned for recall and speed. The reranker is tuned for precision, and its value
lies in promoting relevant passages that the first stage placed just below the cut-off.
