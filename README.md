# ⚠️ NOT PRODUCTION READY · AI SLOP

**Это форк [Vladless/Solo_bot](https://github.com/Vladless/Solo_bot), собранный под один проект.**
Большая часть изменений написана с помощью ИИ и проверена только на нашем стенде и нашем проде.

- **Поддержки нет.** Вопросы, баги, обновления — в апстрим [Vladless/Solo_bot](https://github.com/Vladless/Solo_bot).
- **Используйте на свой страх и риск.** Никаких гарантий: ни работоспособности, ни совместимости с апстримом, ни сохранности данных.
- Апстрим не отслеживается: форк стоит на коммите апстрима `25b258f` (26.09.2026), всё новое в апстриме сюда не попадает.

Рабочая ветка — `dev`. Ниже — PR, которые легли поверх апстрима, и зачем каждый.

## Исправления

- [#5](https://github.com/nastyagrifon/Solo_bot/pull/5) — незавершённая покупка больше не висит в FSM, когда клиент вернулся в профиль.
- [#6](https://github.com/nastyagrifon/Solo_bot/pull/6) — покупка при активном пробнике продлевает его, а не создаёт второй ключ.
- [#8](https://github.com/nastyagrifon/Solo_bot/pull/8) — пробник предлагается только тем, у кого нет подписки, во всех местах одинаково.
- [#18](https://github.com/nastyagrifon/Solo_bot/pull/18) — история баланса: каждая операция отдельной цитатой.
- [#42](https://github.com/nastyagrifon/Solo_bot/pull/42) — KassaAI, Heleket и ParityPay читают включение из настроек админки, а не из конфига при импорте.
- [#56](https://github.com/nastyagrifon/Solo_bot/pull/56) — экраны админки показывают Telegram ID клиента вместо внутреннего id.
- [#58](https://github.com/nastyagrifon/Solo_bot/pull/58) — профиль открывается без FSM-состояния в режиме одной подписки.
- [#94](https://github.com/nastyagrifon/Solo_bot/pull/94) — не пытаться редактировать сообщение клиента перед ответом: минус один пустой запрос на каждый /start.

## Новое

- [#7](https://github.com/nastyagrifon/Solo_bot/pull/7) — хук `purchase_confirm`: модуль может забрать оформление покупки себе.
- [#10](https://github.com/nastyagrifon/Solo_bot/pull/10) — приём вебхуков панели Remnawave в ядре с проверкой подписи и раздачей через хук.
- [#13](https://github.com/nastyagrifon/Solo_bot/pull/13) — горячий перезапуск модуля снимает и его HTTP-маршруты, middleware и фоновые задачи, а не только роутер.
- [#16](https://github.com/nastyagrifon/Solo_bot/pull/16) — платёжные провайдеры грузятся по требованию, в том числе включённые из админки на лету.
- [#22](https://github.com/nastyagrifon/Solo_bot/pull/22) — упрощение runtime горячей загрузки и ленивой загрузки касс, поведение то же.
- [#92](https://github.com/nastyagrifon/Solo_bot/pull/92) — журнал медленных ожиданий и проглоченных ошибок (Redis, Telegram, пул потоков, event loop).
- [#96](https://github.com/nastyagrifon/Solo_bot/pull/96) — слоты A/B: второй процесс ядра, готовность, лидер периодики, защита вебхука — переключение без простоя.
- [#98](https://github.com/nastyagrifon/Solo_bot/pull/98) — слот ждёт встроенный API, порт API на каждый слот свой.

## Рефакторинг (поведение не меняется)

- [#24](https://github.com/nastyagrifon/Solo_bot/pull/24) — удалены неиспользуемые копии рефералок и кэшбэка.
- [#26](https://github.com/nastyagrifon/Solo_bot/pull/26) — удалён probe-middleware за намертво выключенным флагом.
- [#28](https://github.com/nastyagrifon/Solo_bot/pull/28) — Google и Yandex OAuth из одной фабрики маршрутов.
- [#30](https://github.com/nastyagrifon/Solo_bot/pull/30) — один сценарий пополнения баланса на шесть провайдеров.
- [#32](https://github.com/nastyagrifon/Solo_bot/pull/32) — периодические задачи регистрируются из двух таблиц, а не копипастой.
- [#34](https://github.com/nastyagrifon/Solo_bot/pull/34) — CLI без заглушек на случай отсутствия rich.
- [#36](https://github.com/nastyagrifon/Solo_bot/pull/36) — общая загрузка ключа и каркас экранов между режимами дополнений.
- [#38](https://github.com/nastyagrifon/Solo_bot/pull/38) — один обработчик произвольной суммы в быстрой оплате.
- [#40](https://github.com/nastyagrifon/Solo_bot/pull/40) — метод быстрой оплаты берётся из таблицы провайдеров, обёртки на метод удалены.
- [#44](https://github.com/nastyagrifon/Solo_bot/pull/44) — тонкие обёртки для заморозки ключа, названий месяцев и проверки кластера.
- [#46](https://github.com/nastyagrifon/Solo_bot/pull/46) — один обработчик на четыре лид-скидки.
- [#48](https://github.com/nastyagrifon/Solo_bot/pull/48) — один цикл load/update для runtime-настроек вместо тринадцати копий.
- [#50](https://github.com/nastyagrifon/Solo_bot/pull/50) — общая строка пагинации для клавиатур админки.
- [#52](https://github.com/nastyagrifon/Solo_bot/pull/52) — один конфигуратор тарифа для цен по устройствам и по трафику.
- [#54](https://github.com/nastyagrifon/Solo_bot/pull/54) — удалены имена ядра, оставленные только ради внешних модулей.
- [#60](https://github.com/nastyagrifon/Solo_bot/pull/60) — удалены мёртвые флаги, обработчики и помощники.
- [#62](https://github.com/nastyagrifon/Solo_bot/pull/62) — один экран конфигурации продления.
- [#64](https://github.com/nastyagrifon/Solo_bot/pull/64) — общие экраны подключения приложения по платформам.
- [#66](https://github.com/nastyagrifon/Solo_bot/pull/66) — меню быстрой оплаты собирается один раз.
- [#68](https://github.com/nastyagrifon/Solo_bot/pull/68) — один выбор серверов для подгрупп и спецгрупп кластера.
- [#70](https://github.com/nastyagrifon/Solo_bot/pull/70) — один выбор хостов и нод Remnawave.
- [#72](https://github.com/nastyagrifon/Solo_bot/pull/72) — один помощник для переключателей настроек вкл/выкл.
- [#74](https://github.com/nastyagrifon/Solo_bot/pull/74) — общее создание купонов на баланс, дни и процент.
- [#76](https://github.com/nastyagrifon/Solo_bot/pull/76) — один путь «подписка не найдена» для экранов ключей в админке.
- [#78](https://github.com/nastyagrifon/Solo_bot/pull/78) — одна проверка владельца ключа для экранов подключения и удаления.
- [#80](https://github.com/nastyagrifon/Solo_bot/pull/80) — один CSV-писатель для всех экспортов.
- [#82](https://github.com/nastyagrifon/Solo_bot/pull/82) — общий chat id, уведомление и откат на стартовый экран в обработчике ошибок.
- [#84](https://github.com/nastyagrifon/Solo_bot/pull/84) — обёртки Message для кастомных эмодзи из одной фабрики.
- [#86](https://github.com/nastyagrifon/Solo_bot/pull/86) — удалён Redis-кэш экрана аудита по пользователю.
- [#88](https://github.com/nastyagrifon/Solo_bot/pull/88) — удалён неиспользуемый thread-режим cron и обёртки задач из одного поля.
- [#90](https://github.com/nastyagrifon/Solo_bot/pull/90) — один цикл батчей в `check_notifications_bulk`, один `resolve_cluster`.


<hr style="height:1px;border:0;background:#222;margin:18px 0 16px">

<h2 align="center">Лицензия</h2>
<table align="center" style="max-width:900px;width:100%">
  <tr>
    <td style="width:8%;text-align:center">❗</td>
    <td><b>Этот проект распространяется по лицензии <a href="LICENSE">CC BY-NC 4.0</a></b></td>
  </tr>
  <tr>
    <td style="text-align:center">⛔</td>
    <td><b>Перепродажа кода запрещена.</b> Нельзя продавать или перепродавать код без разрешения автора.</td>
  </tr>
  <tr>
    <td style="text-align:center">✅</td>
    <td><b>Для личного использования.</b> Код можно использовать и модифицировать для личных проектов.</td>
  </tr>
</table>
<hr style="height:1px;border:0;background:#222;margin:18px 0 16px">

<h2 align="center">Участники</h2>
<p align="center">
  <a href="https://github.com/Vladless/Solo_bot/graphs/contributors">
    <img src="https://contrib.rocks/image?repo=Vladless/Solo_bot" alt="Contributors">
  </a>
</p>


