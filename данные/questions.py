"""Банк вопросов теста и логика подсчёта по пяти стилям.

Как считается результат:
- у каждого варианта ответа есть веса по стилям (вопросы 1-7 дают по 2
  балла одному стилю, но структура позволяет ставить баллы нескольким);
- баллы всех ответов складываются, побеждает стиль с максимальной суммой;
- если максимум набрали два и более стиля, результат считается ничьей:
  бот покажет оба стиля и совет, как их объединить.

Максимум баллов: 7 вопросов * 2 балла = 14.
"""

from данные.styles import STYLES

QUESTIONS = [
    {
        "id": 1,
        "text": "Какие пол и стены вам ближе?",
        "image": "вопрос_1.jpg",
        "options": [
            {
                "text": "Светлое дерево и белые стены",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Бетон, кирпич, тёмные тона",
                "scores": {"loft": 2},
            },
            {
                "text": "Гладкие поверхности и нейтральные оттенки",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Паркет и обои с узором",
                "scores": {"classic": 2},
            },
            {
                "text": "Смешение фактур и материалов",
                "scores": {"eclecticism": 2},
            },
        ],
    },
    {
        "id": 2,
        "text": "Какая цветовая палитра вам приятнее?",
        "image": "вопрос_2.jpg",
        "options": [
            {
                "text": "Белый, светло-серый, голубой",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Тёмно-серый, чёрный, металлик",
                "scores": {"loft": 2},
            },
            {
                "text": "Белый, чёрный, бежевый",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Яркие цвета и контрасты",
                "scores": {"eclecticism": 2},
            },
            {
                "text": "Глубокие насыщенные тона (изумруд, бордо)",
                "scores": {"classic": 2},
            },
        ],
    },
    {
        "id": 3,
        "text": "Какую мебель вы выберете?",
        "image": "вопрос_3.jpg",
        "options": [
            {
                "text": "Простые формы, светлое дерево",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Кожа, металл, состаренное дерево",
                "scores": {"loft": 2},
            },
            {
                "text": "Чистые линии, без декора",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Резная, с историей, антиквариат",
                "scores": {"classic": 2},
            },
            {
                "text": "Смешение эпох и стилей",
                "scores": {"eclecticism": 2},
            },
        ],
    },
    {
        "id": 4,
        "text": "Какое освещение вам больше по душе?",
        "image": "вопрос_4.jpg",
        "options": [
            {
                "text": "Много естественного света, простые лампы",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Индустриальные подвесы, металл",
                "scores": {"loft": 2},
            },
            {
                "text": "Скрытая подсветка, точечные светильники",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Хрустальная люстра, бра",
                "scores": {"classic": 2},
            },
            {
                "text": "Смешение разных светильников",
                "scores": {"eclecticism": 2},
            },
        ],
    },
    {
        "id": 5,
        "text": "Какой декор вам ближе?",
        "image": "вопрос_5.jpg",
        "options": [
            {
                "text": "Растения, природный текстиль, минимум вещей",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Постеры, открытые полки, индустриальные объекты",
                "scores": {"loft": 2},
            },
            {
                "text": "Почти нет декора",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Картины, зеркала, винтажные вещи",
                "scores": {"classic": 2},
            },
            {
                "text": "Эклектичная смесь искусства и объектов",
                "scores": {"eclecticism": 2},
            },
        ],
    },
    {
        "id": 6,
        "text": "Какие материалы и фактуры вам нравятся?",
        "image": "вопрос_6.jpg",
        "options": [
            {
                "text": "Дерево, лён, шерсть",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Кирпич, металл, бетон",
                "scores": {"loft": 2},
            },
            {
                "text": "Стекло, гладкие поверхности",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Бархат, шёлк, мрамор",
                "scores": {"classic": 2},
            },
            {
                "text": "Смешение разных текстур",
                "scores": {"eclecticism": 2},
            },
        ],
    },
    {
        "id": 7,
        "text": "Как выглядит комната вашей мечты?",
        "image": "вопрос_7.jpg",
        "options": [
            {
                "text": "Светлая, воздушная, с природными материалами",
                "scores": {"scandinavian": 2},
            },
            {
                "text": "Просторная, индустриальная, с открытой планировкой",
                "scores": {"loft": 2},
            },
            {
                "text": "Чистая, белая, без лишних деталей",
                "scores": {"minimalism": 2},
            },
            {
                "text": "Богатая, элегантная, с деталями",
                "scores": {"classic": 2},
            },
            {
                "text": "Яркая, личная, смелая",
                "scores": {"eclecticism": 2},
            },
        ],
    },
]


# Максимум баллов считаем из данных: 7 вопросов * 2 балла = 14.
MAX_TOTAL = sum(
    max(sum(option["scores"].values()) for option in question["options"])
    for question in QUESTIONS
)


def calculate_result(answers: list[dict]) -> dict:
    """Считает результат теста по весам ответов.

    answers — список словарей вида {"scores": {"loft": 2}}, по одному
    на каждый ответ пользователя.

    Возвращает словарь:
    - is_tie: True, если максимальный балл набрали два стиля или больше;
    - primary: словарь победителя из STYLES;
    - secondary: второй стиль при ничьей, иначе None;
    - tie_styles: все стили с максимальным баллом (для ничьей из трёх);
    - combine_advice: совет, как объединить два стиля при ничьей;
    - scores: баллы по всем пяти стилям;
    - max_score: лучший результат теста;
    - max_total: сколько баллов можно было набрать.
    """
    if not answers:
        raise ValueError("Нет ни одного ответа: считать результат не из чего")

    scores = {style_id: 0 for style_id in STYLES}
    for answer in answers:
        for style_id, points in answer["scores"].items():
            if style_id not in scores:
                raise ValueError(f"Неизвестный стиль в ответе: {style_id}")
            scores[style_id] += points

    max_score = max(scores.values())
    winners = [
        style_id for style_id, score in scores.items() if score == max_score
    ]
    primary = STYLES[winners[0]]

    if len(winners) == 1:
        return {
            "is_tie": False,
            "primary": primary,
            "secondary": None,
            "tie_styles": [],
            "combine_advice": None,
            "scores": scores,
            "max_score": max_score,
            "max_total": MAX_TOTAL,
        }

    secondary = STYLES[winners[1]]
    combine_advice = (
        f"Начните с базы в духе «{primary['title']}», а детали добавьте "
        f"из «{secondary['title']}»: два-три предмета или акцента, "
        "не больше. Общая палитра и одинаковые материалы свяжут стили "
        "в одно целое."
    )
    return {
        "is_tie": True,
        "primary": primary,
        "secondary": secondary,
        "tie_styles": [STYLES[style_id] for style_id in winners],
        "combine_advice": combine_advice,
        "scores": scores,
        "max_score": max_score,
        "max_total": MAX_TOTAL,
    }
