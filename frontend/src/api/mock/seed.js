// Синтетические данные пяти тестовых компаний (SCOPE, раздел 12). Только для режима VITE_USE_MOCKS.

export const COMPANIES = {
  "7709876543": { name: "ООО «Пекарня Колос»", inn: "7709876543", ogrn: "1217700456789", registered_at: "2021-09-14", status: "Действующая", okved: "10.71 — Производство хлеба и мучных кондитерских изделий", director: "Анна Смирнова", founders: ["Анна Смирнова"], address: "г. Москва, ул. Профсоюзная, д. 12" },
  "7705123456": { name: "ООО «Ромашка»", inn: "7705123456", ogrn: "1167746123456", registered_at: "2016-04-02", status: "Ликвидирована", okved: "47.11 — Торговля в неспециализированных магазинах", director: "Анна Смирнова", founders: ["Анна Смирнова"], address: "г. Москва, ул. Лесная, д. 5" },
  "503112345678": { name: "ИП Соколов", inn: "503112345678", ogrn: "321503100012345", registered_at: "2022-03-10", status: "Действующий", okved: "47.52 — Торговля строительными материалами", director: "Анна Смирнова", founders: [], address: "Московская обл., г. Химки" },
  "771234567812": { name: "ИП Иванова", inn: "771234567812", ogrn: "322774600012345", registered_at: "2025-03-18", status: "Действующий", okved: "56.30 — Подача напитков", director: "Анна Смирнова", founders: [], address: "г. Москва" },
  "5029123456": { name: "ООО «Техпром»", inn: "5029123456", ogrn: "1195029001234", registered_at: "2019-02-11", status: "Действующая", okved: "25.62 — Обработка металлических изделий", director: "Анна Смирнова", founders: ["Анна Смирнова"], address: "Московская обл., г. Мытищи, ул. Заводская, д. 3" },
};

export const USERS = [
  { id: 1, login: "bakery", full_name: "Анна Смирнова", role: "CLIENT", inns: ["7709876543"], has_account: true },
  { id: 2, login: "romashka", full_name: "Анна Смирнова", role: "CLIENT", inns: ["7705123456"], has_account: true },
  { id: 3, login: "sokolov", full_name: "Анна Смирнова", role: "CLIENT", inns: ["503112345678"], has_account: true },
  { id: 4, login: "ivanova", full_name: "Анна Смирнова", role: "CLIENT", inns: ["771234567812"], has_account: false },
  { id: 5, login: "techprom", full_name: "Анна Смирнова", role: "CLIENT", inns: ["5029123456"], has_account: true },
  { id: 6, login: "analyst", full_name: "Кредитный аналитик", role: "ANALYST", inns: [] },
];

export const PASSWORD = "demo";

export const PURPOSES = [
  { code: "ONE_TIME_PURCHASE", label: "Разовая закупка" },
  { code: "GROWTH", label: "Развитие бизнеса" },
  { code: "REFINANCING", label: "Рефинансирование" },
  { code: "REGULAR_PURCHASES", label: "Регулярные закупки" },
  { code: "SEASONAL_GAP", label: "Сезонный разрыв" },
];

export const COLLATERAL_TYPES = [
  { code: "PRODUCTION_PREMISES", label: "Производственное помещение" },
  { code: "COMMERCIAL_PREMISES", label: "Коммерческая недвижимость" },
  { code: "EQUIPMENT", label: "Оборудование" },
  { code: "VEHICLES", label: "Транспорт" },
];

export const PRODUCTS = [
  {
    code: "LOAN",
    name: "Кредит одной суммой",
    variants: [
      { code: "UNSECURED", name: "Без залога", amount_min: 300000, amount_max: 30000000, term_min: 12, term_max: 60, rate_min: 23.5, collateral_required: false },
      { code: "SECURED", name: "С залогом", amount_min: 500000, amount_max: 150000000, term_min: 12, term_max: 120, rate_min: 21.5, collateral_required: true },
    ],
  },
  {
    code: "CREDIT_LINE",
    name: "Кредитная линия",
    tranche_min: 50000,
    variants: [
      { code: "UNSECURED", name: "Без залога", amount_min: 500000, amount_max: 20000000, term_min: 12, term_max: 36, rate_min: 24.5, collateral_required: false },
      { code: "SECURED", name: "С залогом", amount_min: 1000000, amount_max: 100000000, term_min: 12, term_max: 60, rate_min: 22.5, collateral_required: true },
    ],
  },
];

export const RECOMMENDATIONS = {
  ONE_TIME_PURCHASE: { recommended: "LOAN", allowed: ["LOAN"], title: "Рекомендуем: кредит одной суммой", explanation: "Для разовой закупки подходит кредит одной суммой: получаете всё сразу и гасите равными платежами" },
  GROWTH: { recommended: "LOAN", allowed: ["LOAN", "CREDIT_LINE"], title: "Рекомендуем: кредит одной суммой", explanation: "Для развития бизнеса обычно берут кредит одной суммой на срок окупаемости вложений" },
  REFINANCING: { recommended: "LOAN", allowed: ["LOAN"], title: "Рекомендуем: кредит одной суммой", explanation: "Чтобы закрыть другие кредиты, нужна вся сумма сразу — подойдёт кредит одной суммой" },
  REGULAR_PURCHASES: { recommended: "CREDIT_LINE", allowed: ["CREDIT_LINE", "LOAN"], title: "Рекомендуем: кредитная линия", explanation: "Для регулярных закупок удобнее кредитная линия: берёте деньги частями, когда нужно, а погашенное снова доступно" },
  SEASONAL_GAP: { recommended: "CREDIT_LINE", allowed: ["CREDIT_LINE"], title: "Рекомендуем: кредитная линия", explanation: "Для сезонного разрыва удобнее кредитная линия: берёте деньги частями, когда нужно, а погашенное снова доступно" },
};

export const REJECT_REASONS = [
  { code: "LOW_REVENUE", label: "Выручки недостаточно для такой суммы" },
  { code: "COLLATERAL_INSUFFICIENT", label: "Залог не покрывает сумму кредита" },
  { code: "NEGATIVE_HISTORY", label: "В кредитной истории есть просрочки" },
  { code: "OTHER", label: "Другая причина — в комментарии" },
];

export const SOURCES = ["ЕГРЮЛ", "БКИ", "Обороты по счёту", "ФНС", "ФССП", "ЦБ", "Росфинмониторинг"];
