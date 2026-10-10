# ⚠️ NOT PRODUCTION READY · AI SLOP

**Это форк. Базой взят [Vladless/Solo_bot](https://github.com/Vladless/Solo_bot) на коммите `25b258f` (26.09.2026). Все изменения поверх базы — наши.**
Большая часть написана с помощью ИИ и проверена только на нашем стенде и нашем проде.

- **Апстрим к этим изменениям отношения не имеет.** Авторы Vladless/Solo_bot за этот код не отвечают и помочь с ним не смогут — по вопросам о форке к ним не обращаться.
- **Мы поддержки не оказываем и гарантий не даём.** Ни работоспособности, ни совместимости с апстримом, ни сохранности данных. Использование на свой страх и риск.
- Апстрим не отслеживается: всё, что появилось в Vladless/Solo_bot после `25b258f`, сюда не попадает.

Рабочая ветка — `dev`. Ниже — PR, которые легли поверх базы, и зачем каждый.

## Исправления

| PR | Что | Зачем |
|---|---|---|
| [#5](https://github.com/nastyagrifon/Solo_bot/pull/5) | сброс незавершённой покупки при возврате в профиль | покупка не висит в FSM |
| [#6](https://github.com/nastyagrifon/Solo_bot/pull/6) | покупка при активном пробнике продлевает его | нет второго ключа |
| [#8](https://github.com/nastyagrifon/Solo_bot/pull/8) | пробник только без подписки, во всех местах | одно правило вместо трёх |
| [#18](https://github.com/nastyagrifon/Solo_bot/pull/18) | история баланса: операция = отдельная цитата | читаемость |
| [#42](https://github.com/nastyagrifon/Solo_bot/pull/42) | KassaAI, Heleket, ParityPay читают включение из админки | выключатель в админке работает |
| [#56](https://github.com/nastyagrifon/Solo_bot/pull/56) | Telegram ID клиента в экранах админки | внутренний id никому не нужен |
| [#58](https://github.com/nastyagrifon/Solo_bot/pull/58) | профиль без FSM-состояния в режиме одной подписки | нет ошибки при открытии |
| [#94](https://github.com/nastyagrifon/Solo_bot/pull/94) | не редактировать сообщение клиента перед ответом | минус один пустой запрос на /start |
| [#101](https://github.com/nastyagrifon/Solo_bot/pull/101) | скрытые хосты не попадают в статус серверов кабинета | клиент не видит то, что спрятано |
| [#102](https://github.com/nastyagrifon/Solo_bot/pull/102) | трафик панели сопоставляется и по `vlessUuid`, и по `username` | история трафика была пуста у половины клиентов |
| [#104](https://github.com/nastyagrifon/Solo_bot/pull/104) | раздел кабинета уходит на `tg_id`, а не на внутренний id; выключатель кнопки веб-кабинета | отправка падала у любого клиента с ложной причиной «не запускал бота» |
| [#105](https://github.com/nastyagrifon/Solo_bot/pull/105) | один разбор `X-Forwarded-For` с доверенным списком прокси | адрес клиента в истории входов и в ключе rate-limit нельзя подделать заголовком |

## Новое

| PR | Что | Зачем |
|---|---|---|
| [#7](https://github.com/nastyagrifon/Solo_bot/pull/7) | хук `purchase_confirm` | модуль забирает оформление покупки |
| [#10](https://github.com/nastyagrifon/Solo_bot/pull/10) | приём вебхуков Remnawave в ядре, подпись HMAC | события панели без триггеров в БД |
| [#13](https://github.com/nastyagrifon/Solo_bot/pull/13) | учёт ресурсов модуля: маршруты, middleware, задачи | горячая перезагрузка модуля целиком |
| [#16](https://github.com/nastyagrifon/Solo_bot/pull/16) | загрузка касс по требованию | включение кассы без перезапуска |
| [#22](https://github.com/nastyagrifon/Solo_bot/pull/22) | упрощение runtime горячей загрузки | меньше кода, то же поведение |
| [#92](https://github.com/nastyagrifon/Solo_bot/pull/92) | журнал медленных ожиданий и проглоченных ошибок | видно, где висит клик |
| [#96](https://github.com/nastyagrifon/Solo_bot/pull/96) | слоты A/B: второй процесс, готовность, лидер периодики | перезагрузка бота без простоя |
| [#98](https://github.com/nastyagrifon/Solo_bot/pull/98) | слот ждёт встроенный API, порт API на слот | готовность не врёт |

## Рефакторинг (поведение не меняется)

| PR | Что | Зачем |
|---|---|---|
| [#24](https://github.com/nastyagrifon/Solo_bot/pull/24) | удаление копий рефералок и кэшбэка | мёртвый код |
| [#26](https://github.com/nastyagrifon/Solo_bot/pull/26) | удаление probe-middleware | флаг был выключен намертво |
| [#28](https://github.com/nastyagrifon/Solo_bot/pull/28) | Google и Yandex OAuth из одной фабрики | две копии в одну |
| [#30](https://github.com/nastyagrifon/Solo_bot/pull/30) | один сценарий пополнения на шесть касс | шесть копий в одну |
| [#32](https://github.com/nastyagrifon/Solo_bot/pull/32) | периодические задачи из двух таблиц | регистрация без копипасты |
| [#34](https://github.com/nastyagrifon/Solo_bot/pull/34) | CLI без заглушек rich | 140 строк мёртвого кода |
| [#36](https://github.com/nastyagrifon/Solo_bot/pull/36) | общий каркас экранов дополнений | два режима, один код |
| [#38](https://github.com/nastyagrifon/Solo_bot/pull/38) | один обработчик произвольной суммы в быстрой оплате | шесть копий в одну |
| [#40](https://github.com/nastyagrifon/Solo_bot/pull/40) | метод быстрой оплаты из таблицы провайдеров | нет обёрток на метод |
| [#44](https://github.com/nastyagrifon/Solo_bot/pull/44) | тонкие обёртки: заморозка ключа, месяцы, кластер | один UPDATE вместо двух |
| [#46](https://github.com/nastyagrifon/Solo_bot/pull/46) | один обработчик четырёх лид-скидок | четыре копии в одну |
| [#48](https://github.com/nastyagrifon/Solo_bot/pull/48) | один цикл load/update runtime-настроек | 13 копий в одну |
| [#50](https://github.com/nastyagrifon/Solo_bot/pull/50) | общая строка пагинации админки | семь копий в одну |
| [#52](https://github.com/nastyagrifon/Solo_bot/pull/52) | один конфигуратор тарифа: устройства и трафик | два близнеца в одного |
| [#54](https://github.com/nastyagrifon/Solo_bot/pull/54) | удаление имён ядра ради внешних модулей | мёртвые реэкспорты |
| [#60](https://github.com/nastyagrifon/Solo_bot/pull/60) | удаление мёртвых флагов, обработчиков, помощников | мёртвый код |
| [#62](https://github.com/nastyagrifon/Solo_bot/pull/62) | один экран конфигурации продления | три копии в одну |
| [#64](https://github.com/nastyagrifon/Solo_bot/pull/64) | общие экраны подключения по платформам | пять копий в две |
| [#66](https://github.com/nastyagrifon/Solo_bot/pull/66) | меню быстрой оплаты собирается один раз | две копии в одну |
| [#68](https://github.com/nastyagrifon/Solo_bot/pull/68) | один выбор серверов для подгрупп и спецгрупп | две копии в одну |
| [#70](https://github.com/nastyagrifon/Solo_bot/pull/70) | один выбор хостов и нод Remnawave | две копии в одну |
| [#72](https://github.com/nastyagrifon/Solo_bot/pull/72) | один помощник переключателей вкл/выкл | пять копий в одну |
| [#74](https://github.com/nastyagrifon/Solo_bot/pull/74) | общее создание купонов: баланс, дни, процент | три копии в одну |
| [#76](https://github.com/nastyagrifon/Solo_bot/pull/76) | один путь «подписка не найдена» в админке | 20 копий в одну |
| [#78](https://github.com/nastyagrifon/Solo_bot/pull/78) | одна проверка владельца ключа | две копии в одну |
| [#80](https://github.com/nastyagrifon/Solo_bot/pull/80) | один CSV-писатель для экспортов | семь копий в одну |
| [#82](https://github.com/nastyagrifon/Solo_bot/pull/82) | общий откат на стартовый экран в обработчике ошибок | один путь вместо трёх |
| [#84](https://github.com/nastyagrifon/Solo_bot/pull/84) | обёртки Message для кастомных эмодзи из фабрики | семь копий в одну |
| [#86](https://github.com/nastyagrifon/Solo_bot/pull/86) | удаление Redis-кэша экрана аудита | кэш не окупался |
| [#88](https://github.com/nastyagrifon/Solo_bot/pull/88) | удаление thread-режима cron и обёрток задач | мёртвый код |
| [#90](https://github.com/nastyagrifon/Solo_bot/pull/90) | один цикл батчей уведомлений, один `resolve_cluster` | четыре ветки в одну |

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


