def normalize_optional_text(raw_value):
    return raw_value.strip() or None if raw_value else None


def move_within_siblings(ordered_siblings, public_id, offset):
    """Swap display_order between the item matching public_id and its
    neighbor `offset` positions away. Returns (item, moved) -- item is
    None if public_id was not found in ordered_siblings; moved is False
    when the item exists but the requested move is out of bounds.
    """
    index = next((i for i, item in enumerate(ordered_siblings) if item.public_id == public_id), None)
    if index is None:
        return None, False

    target_index = index + offset
    if target_index < 0 or target_index >= len(ordered_siblings):
        return ordered_siblings[index], False

    current, neighbor = ordered_siblings[index], ordered_siblings[target_index]
    current.display_order, neighbor.display_order = neighbor.display_order, current.display_order
    return current, True
