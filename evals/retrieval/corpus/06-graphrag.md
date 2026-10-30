# GraphRAG

## How it works

GraphRAG, published by Microsoft Research in 2024, uses a language model to extract entities and the
relationships between them from every chunk of a corpus, building a knowledge graph. The Leiden
algorithm then partitions the graph into communities of closely related entities, and the language
model writes a summary of each community.

## Local and global questions

For a local question about specific entities, GraphRAG starts from those entities in the graph and
gathers their neighbours and the text that mentions them. For a global question about a whole
corpus, such as what its main themes are, it answers from the community summaries in a map-reduce
pass. Plain vector search struggles with global questions, because no single passage contains the
answer.

## Cost

Indexing requires language-model calls for every chunk, which makes it far more expensive than
computing embeddings, and adding documents changes the communities, so the graph must often be
rebuilt.
