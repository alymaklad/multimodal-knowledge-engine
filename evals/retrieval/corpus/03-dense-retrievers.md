# Dense retrievers

## Dense Passage Retrieval

Dense Passage Retrieval, or DPR, was introduced by Karpukhin and colleagues in 2020 for open-domain
question answering. It uses two separate BERT encoders, one for questions and one for passages, and
scores a passage by the dot product of the two vectors. This design is called a bi-encoder: every
passage can be encoded once, ahead of time, and stored in a vector index.

## Training with in-batch negatives

DPR is trained contrastively. For each question, the passages that answer the other questions in the
same training batch are reused as negative examples. These in-batch negatives make training efficient,
because one batch of B questions yields B times B comparisons for the price of encoding each text once.
Adding a hard negative, a passage that BM25 ranks highly but that does not contain the answer,
improves the retriever further.

## Limits

Bi-encoders compress a whole passage into one vector, so they can miss exact identifiers and rare
names that a lexical method would match directly.
