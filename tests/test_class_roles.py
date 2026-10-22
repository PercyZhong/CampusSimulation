"""Class identity stays deterministic without adding duplicate student actors."""

from campus_sim.domain import Config
from campus_sim.population import make_population, make_staff, make_teachers


def test_classes_have_one_student_committee_member_and_teacher():
    config = Config(n_students=12, class_support_enabled=True)
    students, _ = make_population(config)
    teachers = make_teachers(config)
    assert [teacher["class_id"] for teacher in teachers] == [
        "class-000", "class-001", "class-002"]
    assert [student["id"] for student in students if student["class_committee"]] == [
        "s000", "s005", "s010"]
    assert [sum(student["class_id"] == teacher["class_id"] for student in students)
            for teacher in teachers] == [5, 5, 2]
    assert all(staff["role"] == "student_adviser" for staff in make_staff(config))


def test_legacy_population_keeps_class_fields_out_of_student_state():
    config = Config(n_students=6)
    students, _ = make_population(config)
    assert all("class_id" not in student and "class_committee" not in student
               for student in students)
    assert all(staff["role"] == "counselor" for staff in make_staff(config))
