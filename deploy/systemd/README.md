# Проверка монтирований Storage Space

Это **системные** unit-файлы, подготовленные в проекте. Они пока не установлены
в `/etc/systemd/system` и таймер не включён.

В `mounts.conf` перечисляются удалённые файловые системы. Каждая строка содержит
три поля через пробел: абсолютный путь монтирования на хосте, путь в контейнере
backend и фактический тип файловой системы (`fuse.sshfs`, `nfs` и т. п.).
Пути с пробелами не поддерживаются. Пустые строки и строки с `#` игнорируются.
При добавлении хранилища также нужно добавить соответствующий bind mount в
`docker-compose.yml` и запись в `STORAGES_JSON`.

Таймер запускает проверку каждую минуту. Скрипт пытается активировать
`x-systemd.automount` простым обращением к каталогу, затем проверяет тип
файловой системы на хосте и внутри backend. Недоступные монтирования пропускает.
Если контейнер не видит хотя бы одно доступное монтирование, backend
пересоздаётся **один раз**. Активная передача при этом прервётся; загрузку
можно продолжить повторным выбором файла.

Unit запускается системным systemd, но сам скрипт работает от `devman` с
доступом к группе `docker`. Общий Docker и другие проекты он не меняет.

Безопасная проверка логики без пересоздания контейнера:

```sh
bash deploy/systemd/check-sshfs-bind.sh --dry-run
```

Установка, когда будешь готов:

```sh
cd /home/devman/workspace/storagespace
sudo install -m 0644 deploy/systemd/storagespace-sshfs-check.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/storagespace-sshfs-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now storagespace-sshfs-check.timer
```

Разовая проверка и просмотр журнала:

```sh
sudo systemctl start storagespace-sshfs-check.service
systemctl status storagespace-sshfs-check.timer
journalctl -u storagespace-sshfs-check.service -n 50 --no-pager
```
