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
            {"label": "Announcements", "endpoint": "admin.announcements_overview"},
            {"label": "Calendar", "endpoint": "admin.calendar"},
        ],
    },
    {
        "label": "Finance & Research",
        "links": [
            {"label": "Billing Desk", "endpoint": "admin.billing_desk"},
            {"label": "Fee Plans", "endpoint": "admin.fee_plans_list"},
            {"label": "Payments", "endpoint": "admin.payments_overview"},
            {"label": "Financial reports", "endpoint": "admin.financial_reports_index"},
            {"label": "Research", "endpoint": None},
        ],
    },
]
