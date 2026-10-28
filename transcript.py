"""Assign recognised speech to exactly one cut."""

def slice_transcript(segments, cuts: list[dict]) -> list[str]:
    """Assign each word once, to the cut containing its temporal midpoint."""
    buckets: list[list[str]] = [[] for _ in cuts]
    for segment in segments:
        words = getattr(segment, "words", None)
        tokens = [(word.start, word.end, word.word) for word in words] if words else [(segment.start, segment.end, segment.text)]
        for start, end, text in tokens:
            midpoint = (start + end) / 2
            for index, cut in enumerate(cuts):
                if cut["start"] <= midpoint < cut["end"]:
                    buckets[index].append(text)
                    break
    return ["".join(words).strip() for words in buckets]
