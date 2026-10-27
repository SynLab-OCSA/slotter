#!/usr/bin/env python3

if __name__ == '__main__':
    # Re-run with "python" of $SLOTTER_DIR/setup.toml before the
    # imports that need it: see src/bootstrap.py
    from src.bootstrap import use_cluster_python
    use_cluster_python()

import os, sys
import smtplib

from src.config import CONFIG


def send_mail(mail, directory, jobid=None, outcome=None):
    if not mail or mail == 'None':
        print('No email address specify, email NOT sent')
        return
    smtp = CONFIG['email']['smtp_server']
    if not smtp:
        print('No SMTP server configured, email NOT sent')
        return
    sender = CONFIG['email']['sender']
    jobid = jobid or os.path.basename(os.path.normpath(directory))
    try:
        # The folder of the calculation, or its README (several calculations in one folder)
        readme = directory if os.path.isfile(directory) else os.path.join(directory, 'README.md')
        with open(readme) as f:
            text = f.read()

        text = f'''From: SLURM <{sender}>
Subject: Calculation of {jobid}{': ' + outcome if outcome else ''}

{text}
        '''

        smtpObj = smtplib.SMTP(smtp)
        smtpObj.sendmail(sender, mail, text)
        print('Email correctly sent')

    except (smtplib.SMTPException, OSError):
        print('Email NOT sent')

if __name__=='__main__':
    # email_sender.py <mail> <directory or README> [<job id> [<outcome>]]: jobs written by
    # older versions of slotter pass only the first two or three
    send_mail(*sys.argv[1:5])
