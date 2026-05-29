
# Para dejarlo “en segundo plano” realmente 
# Ejecutarlo con nohup

source venv/bin/activate
nohup python3 scanner.py > scanner.log 2>&1 &

nohup /home/mint/hscanner/hlbot/bin/python s3-scanner.py --rsi 60 > nohup-scanner.log 2>&1 &

ps aux | grep s3-scanner.py
pgrep -af scanner.py
htop

pkill -f s3-scanner.py
kill PID
kill -9 1234


# systemd service
# - auto restart
# - inicia al boot
# - logs integrados
# - mucho más estable.

/etc/systemd/system/scanner.service

[Unit]
Description=Crypto Scanner

[Service]
ExecStart=/usr/bin/python3 /home/tuusuario/scanner.py
WorkingDirectory=/home/tuusuario
Restart=always

[Install]
WantedBy=multi-user.target

sudo systemctl daemon-reload
sudo systemctl enable scanner
sudo systemctl start scanner

journalctl -u scanner -f

