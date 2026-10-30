# Chunking strategies

## Size and overlap

Documents are split into chunks before they are embedded. Chunks that are too large blur several
topics into one vector; chunks that are too small lose the context needed to understand them. An
overlap between consecutive chunks keeps a sentence that straddles a boundary retrievable in one piece.

## Contextual retrieval

A chunk taken out of its document often loses what it refers to: a sentence saying that revenue grew
by three percent does not say which company or which quarter. Contextual retrieval, published by
Anthropic in 2024, prepends a short explanation of each chunk's place in its document before the chunk
is embedded and indexed. Contextual embeddings alone reduced failed retrievals by 35 percent; combined
with contextual BM25 the reduction reached 49 percent, and adding a reranker on top brought it to 67
percent.

## Late chunking

Late chunking, proposed by Jina AI in 2024, runs the whole document through a long-context embedding
model first and only then pools token embeddings into chunk vectors, so each chunk vector is
informed by the text around it.
