ADMIN_NAV_SECTIONS = [
    {
        "label": "Academic Management",
        "links": [
            {"label": "Academic Terms", "endpoint": "admin.academic_terms_list"},
            {"label": "Levels", "endpoint": "admin.levels_list"},
            {"label": "Courses", "endpoint": "admin.courses_list"},
        ],
    },
    {
        "label": "Center Operations",
        "links": [
            {"label": "Students", "endpoint": "admin.students_list"},
            {"label": "Teachers", "endpoint": "admin.teachers_list"},
            {"label": "Groups", "endpoint": "admin.groups_list"},
            {"label": "Schedules", "endpoint": "admin.schedules_overview"},
            {"label": "Attendance", "endpoint": "admin.attendance_overview"},
            {"label": "Grades", "endpoint": "admin.gradebook_overview"},
        ],
    },
    {
        "label": "Finance & Research",
        "links": [
            {"label": "Payments", "endpoint": None},
            {"label": "Research", "endpoint": None},
        ],
    },
]
