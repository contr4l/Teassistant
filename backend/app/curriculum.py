"""Curriculum whitelist and transitive prerequisite validation."""

from app.contracts import Curriculum


class CurriculumIndex:
    def __init__(self, curriculum: Curriculum):
        self.curriculum = curriculum
        self.points = {p.id: p for p in curriculum.points}
        if len(self.points) != len(curriculum.points):
            raise ValueError("duplicate curriculum point")
        for point in curriculum.points:
            self.validate_ids(point.prerequisite_ids)
        self._closures: dict[str, set[str]] = {}
        for point in self.points:
            self._visit(point, set())

    def validate_ids(self, ids):
        unknown = set(ids) - self.points.keys()
        if unknown:
            raise ValueError(f"unknown curriculum points: {sorted(unknown)}")

    def _visit(self, point: str, visiting: set[str]) -> set[str]:
        if point in visiting:
            raise ValueError("curriculum prerequisites contain a cycle")
        if point not in self._closures:
            needed = {point}
            for parent in self.points[point].prerequisite_ids:
                needed |= self._visit(parent, visiting | {point})
            self._closures[point] = needed
        return self._closures[point]

    def required(self, ids) -> set[str]:
        self.validate_ids(ids)
        result: set[str] = set()
        for point in ids:
            result |= self._closures[point]
        return result
