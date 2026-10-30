# Vector databases

## What they store

A vector database stores embeddings together with their payloads, such as the text of a chunk and the
document it came from, and answers nearest-neighbour queries with filters on that payload.

## Qdrant

Qdrant is an open-source vector database written in Rust. It indexes vectors with HNSW and can store
several named vectors per point, including sparse vectors, which lets one collection serve both dense
and lexical-style search. Payload filters are applied during the graph traversal rather than after
it, so a filtered search still returns a full page of results.

## Quantisation

Scalar quantisation stores each vector component in eight bits instead of thirty-two, cutting memory
by a factor of four. Binary quantisation goes further, keeping one bit per component, and relies on
rescoring the top candidates with the original vectors to recover accuracy.
