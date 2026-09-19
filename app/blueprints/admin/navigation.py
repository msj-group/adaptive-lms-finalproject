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
            {"label": "Student Accounts", "endpoint": "admin.student_accounts"},
            {"label": "Invoices", "endpoint": "admin.invoice_register"},
            {"label": "Payments", "endpoint": "admin.payments_overview"},
            {"label": "Fee Plans", "endpoint": "admin.fee_plans_list"},
            {"label": "Financial reports", "endpoint": "admin.financial_reports_index"},
            {"label": "Deleted Records", "endpoint": "admin.deleted_financial_records"},
            {"label": "Research", "endpoint": None},
        ],
    },
]
