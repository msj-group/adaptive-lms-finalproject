# تسليم توحيد واجهات Youth Centre — 2026-10-09

نُفِّذ التوحيد داخل **المجلد الرئيسي AdaptiveEnglishLMS**. الطالب، المعلم، الإدارة والباحث يستخدمون الآن هيكل ST2 المشترك، مع مكونات تشغيلية تناسب كل دور. توجد لوحة More زجاجية تنشأ من الأسفل، مع الحفاظ على Login وتصميم Messages وخلفية المنصة.

هذا تسليم محلي للمراجعة. لم يحدث Push أو نشر Railway أو Baseline Freeze أو تفعيل Study.

## 1. مزامنة المجلد الرئيسي والفرع

- مجلد التنفيذ: `C:\Users\abdul\Desktop\GraduationProject\AdaptiveEnglishLMS`.
- الفرع: `ui/platform-unification-20261009`.
- HEAD الخاص بالتنفيذ: `fe81e34f44fb870b30a2abdf5705e81da9fa1f77`، يليه commit توثيق هذا التقرير. HEAD النهائي وحالة Git مسجلان في `delivery-state.json` داخل معرض المراجعة وفي رسالة التسليم.
- بدأ MAIN على `master` عند `99133899529376436f52c292df09f5894e9e8cc1`، مع ملفات ST2 غير محفوظة في commit.
- مقارنة المحتوى بعد توحيد LF/CRLF أظهرت 11 ملفًا قديمًا متعلقًا بالنشر و12 ملفًا مفقودًا من MAIN؛ بقية العمل المحلي كان من ST2 نفسه. لم توجد تعديلات مستقلة تحتاج دمجًا يدويًا.
- حُفظت الحالة السابقة في الفرع `preserve/main-pre-unification-20261009`، commit `febbf11faebc35a1a7612cc50af35de6d01d7c1c`، قبل الانتقال الآمن إلى فرع التنفيذ المبني على ST2.
- `master` وتاريخه بقيَا محفوظين. Worktree `AdaptiveEnglishLMS-preserve-ST2` بقيت على فرعها الأصلي، نظيفة، عند `113e124`.
- بقي الوسمان annotated: `YC-VA-D1-20261008` و`YC-VA-AT2-20261008`. لا Reset أو Clean أو Force أو إعادة كتابة تاريخ.

## 2. المصدر المستخدم

المصدر: `113e124e2b9b06daf8162f8f6603b94a369156ad` من `preserve/st2-20261008`.

جرى فحص Git المحلي، التاريخ، الملفات المتتبعة وغير المتتبعة، worktrees، و`git ls-remote` للمستودع الرسمي قبل المزامنة. أعيد فحص Remote قبل commits التسليم: فرع النشر ما زال عند `113e124` و`master` عند `9913389`.

MAIN يحتوي الآن التطبيق الكامل لهذا المصدر، بما فيه إصلاحات Railpack/Python/Gunicorn، مسارات الملفات المتداخلة وقوالب التصدير البحثي المطلوبة للبناء. ملفات النشر لم تتغير ضمن التوحيد.

## 3. جميع الملفات المتغيرة

150 ملف تنفيذ: 149 ملف واجهات وترجمة، وملف واحد لإصلاح عرض إيصالات قديمة. القائمة الكاملة في `docs/PLATFORM_UI_UNIFICATION_FILES_20261009.txt`، وتتضمن أيضًا ملفَي توثيق التسليم.

## 4. الطالب

احتُفظ بتكوين ST2 للدashboard، المقررات، الدروس والأنشطة، مع ترقية foundation/shell إلى ملكية مشتركة. قوالب الطالب القديمة تستخدم includes/shims متوافقة. التغييرات الظاهرة الأساسية: More الجديدة، حد لمس مناسب في أدوات الرأس، وتصحيح التنقل العام في Messages. لم تتغير إجراءات الدروس أو الواجبات أو الاختبارات أو البحث أو الحساب.

## 5. المعلم

هيكل مشترك فعلي مع Sidebar زجاجية ورأس موحد، وDashboard تشغيلية تضم الحصة التالية، أعمال المراجعة، المجموعات والإعلانات. طُبّقت الأسطح والحقول والأزرار والأقسام والتبويبات على المجموعات والوحدات والدروس والمواد والواجبات، إدارة الاختبارات والأسئلة والاستماع والتحدث، مراجعة التسليمات، الحضور، Gradebook، التقدم، الإعلانات والتقويم والنقاشات.

بقيت النماذج ومساراتها وحقولها المخفية وأزرار Save draft/Finalize/Release وقواعدها الأصلية. الجداول القابلة للتحرير تحتفظ بتركيب الجدول، والجداول العريضة لها تمرير مستقل قابل للوصول بلوحة المفاتيح.

## 6. الإدارة

نُقلت Dashboard إلى تكوين تشغيلي من ST2، مع إبقاء الأرقام ومصادرها. شمل التوحيد الفصول، المستويات، المقررات، المجموعات، المدرسين والطلاب، التسجيل، الغرف والجداول، الحضور والدرجات، الإعلانات والتقويم، الحسابات والفواتير والإيصالات والتحصيل والمدفوعات والتصحيحات والمدفوعات الخارجة والتقارير والسجل التاريخي.

أزرار الصفوف، الفلاتر، الإجراءات والنماذج متاحة على المحمول. لا تغييرات في العمليات أو المبالغ أو الحسابات المالية.

## 7. الباحث

شمل التوحيد Dashboard، الإعدادات، الجلسات والتسلسل الزمني والتغطية والجودة والبيانات الناقصة، توزيع الأحداث ودورة Feedback، الاستبعادات والتخزين والتصدير وسجل النشاط. الرسوم تستخدم نفس الخطوط والأسطح والألوان الدلالية، مع جداول أعداد فعلية ومراجع المصدر والنطاق والتوقيت الأصلية.

التسلسل الزمني وFeedback يبقيان جداول كثيفة ذات تمرير مضبوط على المحمول. لم تتغير معادلات المقاييس أو البسط/المقام أو التواريخ أو التصفية أو معنى عدم وجود البيانات.

## 8. النظام المشترك وحدود CSS

- `product/foundation.css`: خطوط Outfit/Inter/Noto Sans Arabic، tokens مشتقة من ST2، الحقول والأزرار والتنبيهات وحالات العرض.
- `product/shell.css`: هيكل التطبيق والرأس والـSidebar وحساب المستخدم والإشعارات.
- `product/mobile-navigation.css`: مالك مستقل للتنقل المحمول ولوحة More؛ يُحمّل وحده في Messages.
- `product/operations.css`: تكوين المعلم والإدارة والصفحات المشتركة التشغيلية.
- `product/research.css`: الرسوم البحثية والجداول الكثيفة.
- `product/community.css`: التقويم والتواصل التشغيلي.
- partials مشتركة للهيكل والوجهات وMobile navigation، وmacro لمقدمة workspace.

لا React runtime. Flask/Jinja/WTForms باقية. CSS التشغيلية لا تُحمّل في Login أو Messages، ولا تُفرض Dashboard الطالب على بقية الأدوار.

## 9. Mobile More

لوحة non-modal مسماة لقارئات الشاشة، بشبكة أربعة أعمدة أو ثلاثة على الشاشات الصغيرة، سطح شبه شفاف، blur وحدود ناعمة وزوايا مستديرة. التمرير داخلي عند الحاجة. شريط التنقل يبقى ظاهرًا وقابلًا للاستخدام.

More تضبط `aria-expanded` وتفتح/تغلق بالتبديل، زر الإغلاق، الخلفية، Escape أو انتقال التركيز خارجها. ينتقل التركيز إلى الإغلاق ثم يعود إلى موضعه عند الإغلاق الصريح. اختيار رابط يحافظ على الانتقال الحقيقي. لوحة المفاتيح واللمس واستعادة التركيز اجتازت الفحص. الحركة 160ms دون bounce، وتُلغى في Reduced Motion.

الوجهات server-owned ومخصصة لكل دور. لا Routes جديدة أو وجهات مفقودة أو نظام Drawer إضافي. More لا تنفذ fetch أو تعديل history أو emit لحدث Page View؛ `nav_more` وcollector الأصلي بقيَا كما هما.

## 10. Messages Hamburger

التحكم المسؤول هو `.figma-menu-toggle[data-nav-toggle]` في الرأس المشترك. عند وجود More وعلى عرض أقل من 1024px، يُخفى التحكم العام، ويتوقف initializer القديم عن إنشاء سلوك Sidebar/Drawer.

عنوان Messages، Back، اختيار المستلم، New conversation، Thread، Composer، Emoji وConversation/Message action menus بقيت. جرى فتح Emoji وقائمة إجراءات المحادثة في حسابَي Student وTeacher والتحقق من ظهور Composer وBack. وصول Admin/Researcher إلى Messages يظل مقيدًا بالقواعد الأصلية؛ لم تُوسّع الصلاحيات.

## 11. دليل حفظ Login

قالب Login و`login.css` وتكوين الرأس/الخلفية والعلامة والخطوط والزر الأحمر لم تتغير. لا تحميل لأي stylesheet من `product/` في Login.

مقارنة الصور قبل/بعد على 1440×960 وعلى 390×960: **صفر بكسل متغير**. Messages Desktop على 1440×960: **صفر بكسل متغير** أيضًا. المقارنة محفوظة في `protected-pixel-comparison.json`.

## 12. دليل حفظ الخلفية

`tokens.css` وخلفية canvas الحالية لم تتغير. body الخلفية الفعلية بقيت `rgb(234, 240, 250)` في Light و`rgb(16, 30, 51)` في Dark. لا ambient gradient على الصفحة أو floating blobs أو حركة خلفية مستمرة. gradients المستخدمة داخل المكونات فقط.

## 13. التحقق الوظيفي

فُحص التطبيق الحقيقي من MAIN عبر Flask وMySQL المحلي وحسابات التطوير الخيالية الأربعة. جلسات التحقق المحلية الموقعة مرت عبر loader وصلاحيات التطبيق الأصلية؛ لم يتغير كود المصادقة. لم يُعاد اختبار إرسال كلمة مرور Login في هذه المهمة.

| الفحص | النتيجة والحدود |
| --- | --- |
| Jinja | 212 قالبًا تجمّع دون أخطاء |
| JavaScript/Python | syntax ناجح للملفات المعدلة |
| عقود النماذج | 135 قالبًا: method/action، macros CSRF والحقول المخفية محفوظة |
| استعراض المسارات | 335 GET: Student 55، Teacher 110، Admin 140، Researcher 30؛ أربع صفحات إيصال قديمة كانت 500 وتم إصلاحها |
| إعادة فحص الإيصالات | الإيصالات الخمسة HTTP 200، بما فيها الأربعة السابقة |
| عرض تشغيلي محمول | 38 صفحة عند 390px Dark دون تجاوز أفقي أو خطأ JS |
| تأليف المعلم | 45 GET إضافية عند 320px Arabic/Dark، تشمل Quiz/listening/speaking/assignments ومحرر الأسئلة والإعدادات والمراجعة، دون أخطاء |
| الحساب المشترك | `/account` HTTP 200 للأدوار الأربعة، دون تجاوز؛ CSRF ذات prefixes الأصلية موجودة |
| تحقق الحقول | form.checkValidity لنموذج طالب فارغ أظهر أربع قيم مطلوبة، دون إرسال النموذج |
| More | فتح وإغلاق وتركيز ولمس وانتقال حقيقي ناجح لكل دور؛ touch targets ≥44px |
| الجداول | مناطق تمرير مسماة وtabIndex=0؛ تمرير Timeline بلوحة المفاتيح تحقق فعليًا |
| Zoom | إعادة تدفق مكافئة لـ200% Desktop عبر 720 CSS px ناجحة؛ zoom الفعلي من شريط Chrome لم يُختبر |

**اختبار جزئي للمعاملات:** النماذج والواجهات والقواعد المصدرية تحققت، لكن Save draft/Finalize/Release، إرسال إجابات/رسائل، المدفوعات، الرفع وإنشاء export لم تُرسل كمعاملات كتابة. حُظر business POST في الفحوص التفاعلية. لا يُدّعى نجاح اختبار كتابة end-to-end لهذه الإجراءات. لا مجموعة اختبارات آلية جديدة ولا تشغيل test suite.

## 14. التوافق البحثي والإنتاجي

363 ملفًا محميًا قورنت بمصدر ST2 مع تجاهل فرق LF/CRLF. الاختلاف الوظيفي الوحيد خارج الواجهات هو عرض الإيصال المذكور أدناه؛ models/services/auth/collector/Event Dictionary/migrations/ملفات Railway بقيت كما هي.

بيئة التحقق: `RESEARCH_DATA_PROVENANCE=development`، و`RAILWAY_ALLOW_STUDY=0`. لم تتغير حالة الإعداد البحثي أو sampling أو provenance أو فصل البيانات أو IDs المعتمدة. لا inference أو frustration predictions أو Version B. لا جمع لنصوص الرسائل أو قيم الحقول أو الإجابات أو أسماء الملفات الخاصة.

لم تحدث migration أو database reset أو حذف للحسابات. لم تُلمس Railway أو درجاتها الـ63 أو حضورها المعلق. قاعدة التطوير المحلية مختلفة: القراءة أظهرت draft grade item واحدًا وdraft grade record واحدًا و0 pending attendance sessions؛ لم تُحفظ أو تُنشر هذه السجلات.

**الاستثناء الضيق المثبت:** أربعة إيصالات محفوظة من schema `repair.student-receipt.v1` تحمل `confirmed_at` بصيغة ISO whole-second دون Z. parser الصارم أدى إلى 500، وأُعيد إنتاج الخطأ قبل تطبيق الإصلاح. فرع العرض فقط يقرأ هذا الشكل القديم، بينما validation/snapshots/payment state/calculations تبقى كما هي. لم تتغير بيانات أي إيصال. التوافق يشمل شكل repair online القديم أيضًا دون تغيير قواعد الإيصال الجديد.

## 15. معرض Desktop/Mobile/Dark/RTL

[فتح معرض المراجعة المحلي](http://127.0.0.1:8766/index.html)

النسخة المحفوظة: `C:\Users\abdul\.codex\visualizations\2026\10\09\01a1208a-f3f9-74e2-afd0-79fbe04fcf48\platform-ui\gallery\index.html`.

84 صورة متاحة داخل معرض عام محلي مستقل عن ملفات جلسات QA. يتضمن Before/After للأدوار الأربعة، Desktop/Mobile، المقاسات الستة، More مغلقة/مفتوحة، Messages Inbox/Thread، Login، Dark Desktop/Mobile وArabic RTL Desktop/Mobile، وعينات تشغيلية.

45 حالة مصورة أولية و144 حالة إضافية للثيمات: كل دور عند 1440/1024/768/430/390/320px مع Dark English، Light Arabic، Dark Arabic، System Dark Arabic، System Light English وReduced Motion. كلها HTTP 200، دون JS errors أو تجاوز أفقي، ولوحة More لا تحجب الشريط.

## 16. الحدود المعروفة

- لم تُفحص كل record أو كل permutation للفلاتر، وإنما page families ومسارات محدودة موثقة.
- لم تُنفذ معاملات أعمال أو إنشاء تصدير/رفع/حفظ/نشر/Finalize؛ يلزم rehearsal مضبوط ببيانات تطوير مخصصة عند قبول الإصدار.
- الفحص على Chrome المحلي مع touch emulation؛ لا فحص جهاز iOS/Android فعلي أو soft keyboard أو notch غير صفري، ولا جلسة NVDA/VoiceOver. safe-area وReduced Motion وRTL والـARIA/التركيز تحققوا في المتصفح والكود.
- نظام الثيمات تحقق في وضعَي الجهاز؛ zoom الفعلي من واجهة المتصفح لم يُختبر، وإنما إعادة التدفق المكافئة.
- launcher البيئة `.venv/Scripts/python.exe` المحلي القديم لم يعمل في سياق التشغيل؛ شُغّل التطبيق بمفسر Python الموجود و`PYTHONPATH` إلى `.venv/Lib/site-packages`. لم تتغير تبعيات التطبيق أو ملفات Railway لمعالجة ذلك.

## 17. Commits

| Commit | المحتوى |
| --- | --- |
| `febbf11faebc35a1a7612cc50af35de6d01d7c1c` | حفظ الحالة السابقة على فرع الأمان، خارج ancestry فرع التنفيذ |
| `aaba9d0d2aa99bdef6eec564405f66c8a3a5c85c` | توحيد الأدوار وMore والحدود المحمية |
| `d2f3267681e54c803e6d9517f6fa4d33f90fa52e` | توافق عرض الإيصالات القديمة فقط |
| `fe81e34f44fb870b30a2abdf5705e81da9fa1f77` | وصول لوحة المفاتيح إلى مناطق تمرير الجداول |

Commit التوثيق النهائي يضم هذا التقرير وقائمة الملفات. معرفه مسجل في رسالة التسليم و`delivery-state.json`. لا Push أو Force-push.

## 18. Git working tree

يُحفظ التقرير والقائمة في commit توثيق منفصل، ثم تُفحص نظافة MAIN وST2 worktree. حالة Git النهائية والـHEAD الكامل في `delivery-state.json` بالمعرض. لا تضم commits أسرارًا أو ملفات جلسات QA أو بيانات خاصة.

## 19. جاهزية MAIN

**MAIN جاهز لمراجعة الواجهات:** مصدر ST2/Railway الكامل وتعديلات الأدوار وMore والرسائل موجودة فيه، على فرع تنفيذ مستقل. جميع تعديلات التطبيق موجودة داخل MAIN؛ المعرض فقط محفوظ في مجلد artifacts.

للتشغيل المحلي المستخدم في التحقق، من MAIN: ضع `PYTHONPATH` على `.venv/Lib/site-packages`، واضبط provenance إلى development وStudy إلى 0، ثم شغّل مفسر Python المثبت مع `run.py` أو factory `app.create_app()`. خادم المراجعة الحالي على `http://127.0.0.1:5002`.

## 20. ما يلزم قبل تحديث Railway

1. قبول المالك للمعرض والتغييرات والحدود المذكورة.
2. rehearsal مضبوط لمعاملات Save draft والرفع وتسليم الأنشطة والمدفوعات والتصدير باستخدام سجلات تطوير مخصصة؛ تجنب الـ63 draft grades والحضور المعلق.
3. فحص جهاز محمول حقيقي وقارئ شاشة عند اعتماد الإصدار.
4. اختيار commits الإصدار بعد القبول ودمجها إلى فرع النشر دون force أو إعادة كتابة التاريخ، ثم Push/Deploy بموافقة منفصلة.
5. إبقاء `RESEARCH_DATA_PROVENANCE=development` و`RAILWAY_ALLOW_STUDY=0`، مع فحص Railway بعد الإصدار دون migration أو reset أو Study activation.

لم تُتخذ أي خطوة من نشر الإصدار أو تغيير baseline تلقائيًا.

## Owner follow-up: compact mobile More

The follow-up removes the visible Youth Centre / More navigation heading and the close button. The panel now uses compact 56px touch areas, tighter spacing and four columns (three below 360px). All visible destination icons share the bottom navigation's 21px size. The Student panel at 390px is 216px tall, compared with approximately 507px in the initial delivery.

This supersedes section 9's close-button and initial-focus description: opening now focuses the first destination. Toggle, outside interaction, Escape, focus departure and focus restoration remain available; the dialog retains an accessible name without a visible heading. Destinations, permissions and research hooks are unchanged.

Read-only browser inspection covered all four roles at 430/390/320px, including Arabic Dark Mode. All twelve pages returned HTTP 200, with no JavaScript errors or horizontal overflow; no header/close control remained inside the panel, and dismissal/focus behavior passed. New screenshots are saved as `compact-more-<role>-<width>.png` in the existing local QA artifact directory. No business POST, push or deployment occurred.

## Owner follow-up: mobile header logo

The shared phone header now displays the existing `images/figma_logo.png` asset instead of the Youth Centre text label. The 48px logo retains its aspect ratio and uses a small white backing for legibility in Light and Dark modes. Its alternative text is localized through the existing language system. Desktop header context, Login and Messages headers are unchanged.

Read-only rendered checks covered every role at 430/390/320px and 1440px. The logo loaded at 48 by 48px on phones, replaced the visible brand text, aligned correctly in RTL, and remained hidden on Desktop. All sixteen pages returned HTTP 200 without JavaScript errors or horizontal overflow. Actual screenshots are saved as `mobile-logo-<role>-<width>.png` in the QA artifact directory. No business submissions, push or deployment occurred.
