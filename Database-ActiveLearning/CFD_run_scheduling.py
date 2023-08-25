### SMX_Automation_simulation_run, tailored for BLUE 12.5.1
### CFD scheduling, monitoring and post-processing script
### to be run locally
### Author: Juan Pablo Valdes,
### Contributors: Paula Pico, Fuyue Liang
### First commit: July, 2023
### Department of Chemical Engineering, Imperial College London

import os
from time import sleep
import pandas as pd
import subprocess
import paramiko
import configparser
import warnings
import json
import numpy as np
import logging
import psutil
import glob

######################## EXCEPTION CLASSES ######################

class JobStatError(Exception):
    """Exception class for qstat exception when job has finished or has been removed from HPC run queue"""
    def __init__(self, message="Output empty on qstat execution, job finished or removed"):
        self.message = message
        super().__init__(self.message)

class ConvergenceError(Exception):
    """Exception class for convergence error on job"""
    def __init__(self, message="Convergence checks from csv have failed, job not converging and will be deleted"):
        self.message = message
        super().__init__(self.message)

class BadTerminationError(Exception):
    """Exception class for bad termination error on job after running"""
    def __init__(self, message="Job run ended on bad termination error"):
        self.message = message
        super().__init__(self.message)

#################################################################


class SimScheduling:

    ### Init function
     
    def __init__(self) -> None:
        pass

    ### Defining individual logging files for each run.

    def set_log(self, log_filename):
        # Create a new logger instance for each process
        logger = logging.getLogger(__name__)

        # Clear existing handlers to avoid duplication
        logger.handlers = []

        # Create a new file handler and set its formatter
        file_handler = logging.FileHandler(log_filename)
        formatter = logging.Formatter(fmt="%(asctime)s - %(message)s", datefmt="[%d/%m/ - %H:%M:%S]")
        file_handler.setFormatter(formatter)

        # Add the file handler to the logger's handlers
        logger.addHandler(file_handler)

        return logger  # Return the logger instance

    ### Checking if a pvpython process is active

    @staticmethod
    def is_pvpython_running():
        for process in psutil.process_iter(['pid', 'name']):
            if process.info['name'] == 'pvpython':
                return True, process.info['pid']
        return False, None

    ### converting dictionary input from psweep run_local into readable JSON format

    @staticmethod
    def convert_to_json(obj):
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, np.int64):
            return int(obj) 
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    ### local run assigning parametric study to HPC handling script and performing overall BLUE workflow

    def localrun(self,pset_dict):
        ###Path and running attributes
        self.pset_dict = pset_dict
        self.case_type = pset_dict['case']
        self.run_ID = pset_dict['run_ID']
        self.local_path = pset_dict['local_path']
        self.save_path = pset_dict['save_path']
        self.run_path = pset_dict['run_path']
        self.run_name = pset_dict['run_name']
        self.usr = pset_dict['user']

        self.main_path = os.path.join(self.run_path,'..')

        ### Logger set-up
        log_filename = f"output_{self.case_type}/output_{self.run_name}.txt"
        log = self.set_log(log_filename)

        dict_str = json.dumps(pset_dict, default=self.convert_to_json, ensure_ascii=False)

        ### First job creation and submission

        HPC_script = 'HPC_run_scheduling.py'
        
        log.info('-' * 100)
        log.info('-' * 100)
        log.info('NEW RUN')
        log.info('-' * 100)
        log.info('-' * 100)

        ### wait time to connect at first, avoiding multiple simultaneuous connections
        #init_wait_time = np.random.RandomState().randint(0,180)
        #sleep(init_wait_time)

        try:
            command = f'python {self.main_path}/{HPC_script} run --pdict \'{dict_str}\''
            jobid, t_wait, status, _ = self.execute_remote_command(command=command,search=0,log=log)
        except (paramiko.AuthenticationException, paramiko.SSHException) as e:
            log.info(f"SSH ERROR: Authentication failed: {e}")
            return {}
        except (ValueError, JobStatError, NameError) as e:
            log.info(f'Exited with message: {e}')
            return {}
            
        ### Job monitor and restarting nested loop. Checks job status and restarts if needed.

        restart = True
        while restart:

            ### job monitoring loop

            log.info('-' * 100)
            log.info('JOB MONITORING')
            log.info('-' * 100)

            try:
                self.jobmonitor(t_wait, status, jobid, self.run_ID, HPC_script,log)
            except (ValueError, NameError, ConvergenceError) as e:
                log.info(f'Exited with message: {e}')
                return {}
            except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                log.info(f"SSH ERROR: Authentication failed: {e}")
                return {}

            ### Job restart execution

            log.info('-' * 100)
            log.info('JOB RESTARTING')
            log.info('-' * 100)

            try:
                log.info('-' * 100)
                command = f'python {self.main_path}/{HPC_script} job_restart --pdict \'{dict_str}\''
                new_jobID, new_t_wait, new_status, ret_bool = self.execute_remote_command(
                    command=command, search=2, log=log
                    )

                log.info('-' * 100)

                ### updating
                jobid = new_jobID
                t_wait = new_t_wait
                status = new_status
                restart = eval(ret_bool)

            except (ValueError,FileNotFoundError,NameError) as e:
                log.info(f'Exited with message: {e}')
                return {}
            except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                log.info(f"SSH ERROR: Authentication failed: {e}")
                return {}

        ### vtk convert job creation and submission

        log.info('-' * 100)
        log.info('VTK CONVERTING')
        log.info('-' * 100)

        try:
            log.info('-' * 100)
            command = f'python {self.main_path}/{HPC_script} vtk_convert --pdict \'{dict_str}\''
            conv_jobid, conv_t_wait, conv_status, _ = self.execute_remote_command(
                command=command,search=0,log=log
                )
            log.info('-' * 100)
        except (paramiko.AuthenticationException, paramiko.SSHException) as e:
            log.info(f"SSH ERROR: Authentication failed: {e}")
            return {}
        except (FileNotFoundError, JobStatError, ValueError, NameError) as e:
            log.info(f'Exited with message: {e}')
            return {}
        
        conv_name = 'Convert' + str(self.run_ID)

        ### job convert monitoring loop

        log.info('-' * 100)
        log.info('JOB MONITORING')
        log.info('-' * 100)

        try:
            self.jobmonitor(conv_t_wait,conv_status,conv_jobid,conv_name,HPC_script,log=log)
        except (ValueError, NameError) as e:
            log.info(f'Exited with message: {e}')
            return {}
        except (paramiko.AuthenticationException, paramiko.SSHException) as e:
            log.info(f"SSH ERROR: Authentication failed: {e}")
            return {}

        ### Downloading files and local Post-processing

        log.info('-' * 100)
        log.info('DOWNLOADING FILES FROM EPHEMERAL')
        log.info('-' * 100)

        try:
            self.scp_download(log)
        except (paramiko.AuthenticationException, paramiko.SSHException) as e:
            log.info(f"SSH ERROR: Authentication failed: {e}")
            return {}

        log.info('-' * 100)
        log.info('PVPYTHON POSTPROCESSING')
        log.info('-' * 100)

        ### Checking if a pvpython is operating on another process, if so sleeps.

        pvpyactive, pid = self.is_pvpython_running()

        while pvpyactive:
            log.info(f'pvpython is active in process ID : {pid}')
            sleep(600)
            pvpyactive, pid = self.is_pvpython_running()

        ### Exectuing post-processing
        dfDSD, IntA = self.post_process(log)
        Nd = dfDSD.size

        log.info('-' * 100)
        log.info('Post processing completed succesfully')
        log.info('-' * 100)
        log.info(f'Number of drops in this run: {Nd}')
        log.info(f'Drop size dist. {dfDSD}')
            
        return {"Nd":Nd, "DSD":dfDSD, "IntA":IntA}
    
    ### calling monitoring and restart function to check in on jobs

    def jobmonitor(self, t_wait, status, jobid, run, HPC_script,log):

        running = True
        chk_counter = 0
        csv_check = True # Option to deacticate csv checks and only run qstat
        
        while running:
            
            ### Setting updated dictionary with jobid from submitted job and csv_check logical gate
            mdict = self.pset_dict   
            mdict['jobID'] = jobid
            mdict['check'] = csv_check
            mdict_str = json.dumps(mdict, default=self.convert_to_json, ensure_ascii=False)
                        
            ### If t_wait>0, job is either running or queieng
            if t_wait>0:

            ### Performing monitoring and waiting processes depending on job status and type
                if (status == 'Q' or status == 'H' or (status == 'R' and 'Convert' in run)):
                    ### If Q or H, wait and qstat later
                    log.info('-' * 100)
                    log.info(f'Job {run} with id: {jobid} has status {status}. Sleeping for:{t_wait/60} mins')
                    log.info('-' * 100)
                    sleep(t_wait-3570)
                    try:
                        ### Execute monitor function in HPC to check job status
                        command = f'python {self.main_path}/{HPC_script} monitor --pdict \'{mdict_str}\''
                        new_jobid, new_t_wait, new_status, _ = self.execute_remote_command(
                            command=command,search=0,log=log
                            )
                        
                        ### update t_wait and job status accordingly
                        t_wait = new_t_wait
                        status = new_status
                        jobid = new_jobid

                        log.info('-' * 100)
                        log.info(f'Job {run} with id {jobid} status is {status}. Updated sleeping time: {t_wait/60} mins')
                    except (JobStatError, ValueError, NameError) as e:  
                        if isinstance(e, JobStatError):

                            log.info('-' * 100)
                            log.info(f'Exited with message: {e}')
                            log.info('-' * 100)
                            log.info(f'JOB {run} FINISHED')
                            log.info('-' * 100)

                            ### Update t_wait and job status for finished job condition, exiting the loop
                            t_wait = 0
                            status = 'F'
                            running = False

                        else:
                            log.info('-' * 100)
                            log.info(f'Exited with message: {e}')
                            log.info('-' * 100)
                            raise e

                    except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                        log.info(f"Authentication failed: {e}")
                        raise e
                
                else:
                    ### Status here is R and not jobconvert, performing convergence checks in HPC monitor
                    n_checks = 60 # has to be larger than 0
                    log.info('-' * 100)
                    log.info(f'Job {run} with id: {jobid} has status {status}. Currently on check {chk_counter} out of {n_checks} checks during runtime.')
                    log.info('-' * 100)

                    ### Initializing run_t_wait with initial run time extracted
                    if chk_counter == 0:
                        run_t_wait = t_wait
                        log.info('-' * 100)
                        log.info('First check to be performed')

                    ### Sleep extra after last check to guarantee job has finished at the end
                    if run_t_wait > (t_wait)/(n_checks+1):
                        log.info('-' * 100)
                        log.info(f'Sleeping for {t_wait/(n_checks+1)/60} mins until next check')
                        sleep(t_wait/(n_checks+1))
                    else:
                        log.info('-' * 100)
                        log.info(f'Final check done, sleeping for {run_t_wait/60} mins until completion')
                        sleep((t_wait/(n_checks+1))* 1.05)

                    try:
                        ### Execute monitor function in HPC to check job status
                        command = f'python {self.main_path}/{HPC_script} monitor --pdict \'{mdict_str}\''
                        _, run_t_wait, run_status, _ = self.execute_remote_command(
                            command=command,search=0,log=log
                            )
                        
                        status = run_status
                        chk_counter += 1

                        log.info('-' * 100)
                        log.info(f'Run time remaining: {run_t_wait/60} mins')

                        log.info('-' * 100)
                        log.info(f'Job {run} with id {jobid} status is {status}. Continuing checks')
                    except (JobStatError, ValueError, NameError, ConvergenceError) as e:  
                        if isinstance(e, JobStatError):

                            log.info('-' * 100)
                            log.info(f'Exited with message: {e}')
                            log.info('-' * 100)
                            log.info(f'JOB {run} FINISHED')
                            log.info('-' * 100)

                            ### Update t_wait and job status for finished job condition, exiting the loop
                            t_wait = 0
                            status = 'F'
                            running = False

                        else:
                            log.info('-' * 100)
                            log.info(f'Exited with message: {e}')
                            log.info('-' * 100)
                            raise e

                    except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                        log.info(f"Authentication failed: {e}")
                        raise e
            else:
                running = False

    ### Executing HPC functions remotely via Paramiko SSH library.

    def execute_remote_command(self,command,search,log):

        ### Read SSH configuration from config file
        config = configparser.ConfigParser()
        config.read(f'config_{self.usr}.ini')
        user = config.get('SSH', 'username')
        key = config.get('SSH', 'password')
        try_logins = ['login.hpc.ic.ac.uk','login-a.hpc.ic.ac.uk','login-b.hpc.ic.ac.uk','login-c.hpc.ic.ac.uk']

        ### Initialize variables for result storage
        jobid, t_wait, status, ret_bool = None, 0, None, None
        exc = None

        for login in try_logins:

            ### Establish an SSH connection using a context manager
            ssh = paramiko.SSHClient()
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            warnings.filterwarnings("ignore", category=ResourceWarning)

            try:
                ssh.connect(login, username=user, password=key)

                stdin, stdout, stderr = ssh.exec_command(command)
                out_lines = []
                for line in stdout:
                    stripped_line = line.strip()
                    log.info(stripped_line)
                    out_lines.append(stripped_line)
                
                ### Extracting exceptions, job id, job wait time, status and restart condition
                results = self.search(out_lines=out_lines,search=search)

                jobid = results.get("jobid", None)
                t_wait = float(results.get("t_wait", 0))
                status = results.get("status", None)
                ret_bool = results.get("ret_bool", None)
                exc = results.get("exception",None)

                ### Handle exceptions
                if exc is not None:
                    if exc == "JobStatError":
                        raise JobStatError('qstat output empty, job finished or deleted from HPC run queue')
                    elif exc == "ValueError":
                        raise ValueError('Exception raised from job sh creation, qstat in job_wait \
                                         or attempting to search restart in job_restart')
                    elif exc == "FileNotFoundError":
                        raise FileNotFoundError('File not found: either .out or .csv files not found when attempting restart \
                                                or vtk/pvd/convert files not found when attempting to convert')
                    elif exc == "ConvergenceError":
                        raise ConvergenceError('Convergence checks on HPC failed, job killed as a result')
                    elif exc == "BadTerminationError":
                        raise BadTerminationError("Job run ended with a bad termination error message in the output file. Check convergence or setup issues")
                    else:
                        raise NameError('Search for exception from log failed')
                    
                if stdin is not None:
                    break

            except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                if login == try_logins[-1]:
                    raise e
                else:
                    log.info(f'SSH connection failed with login {login}, trying again ...')
                    continue

            ### Closing HPC session
            finally:
                if 'stdin' in locals():
                    stdin.close()
                if 'stdout' in locals():
                    stdout.close()
                if 'stderr' in locals():
                    stderr.close()
                ssh.close()
        
        return jobid, t_wait, status, ret_bool
 
    ### Search and extract communications from the functions executed remotely on the HPC

    def search(self,out_lines,search):
        ##### search = 0 : looks for JobID, status and wait time
        ##### search = 1 : looks for wait time, status
        ##### search = 2 : looks for JobID, status and wait time and boolean return values

        ### Define markers and corresponding result keys for different search types
        if search == 0:
            markers = {
                "====JOB_IDS====": "jobid",
                "====WAIT_TIME====": "t_wait",
                "====JOB_STATUS====": "status",
                "====EXCEPTION====" : "exception"
                }
        elif search == 1:
            markers = {
                "====WAIT_TIME====": "t_wait",
                "====JOB_STATUS====": "status",
                "====EXCEPTION====" : "exception"
                }
        elif search == 2:
            markers = {
                "====JOB_IDS====": "jobid",
                "====WAIT_TIME====": "t_wait",
                "====JOB_STATUS====": "status",
                "====RETURN_BOOL====": "ret_bool",
                "====EXCEPTION====" : "exception"
                }
            
        results = {}
        current_variable = None

        for idx, line in enumerate(out_lines):
            if line in markers:
                current_variable = markers[line]
                # Store the value associated with the marker as the value of the current variable
                results[current_variable] = out_lines[idx + 1]


        return results

    ### Download final converted data to local processing machine

    def scp_download(self,log):

        ###Create run local directory to store data
        self.save_path_runID = os.path.join(self.save_path,self.run_name)
        ephemeral_path = f'/rds/general/user/{self.usr}/ephemeral/'

        try:
            os.mkdir(self.save_path_runID)
            log.info(f'Saving folder created at {self.save_path}')
        except:
            pass

        ### Config faile with keys to login to the HPC
        config = configparser.ConfigParser()
        config.read(f'config_{self.usr}.ini')
        user = config.get('SSH', 'username')
        key = config.get('SSH', 'password')
        try_logins = ['login.hpc.ic.ac.uk','login-a.hpc.ic.ac.uk','login-b.hpc.ic.ac.uk','login-c.hpc.ic.ac.uk']

        for login in try_logins:
            
            ### Establish an SSH connection using a context manager
            ssh = paramiko.SSHClient()
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            warnings.filterwarnings("ignore", category=ResourceWarning)

            try:
                ssh.connect(login, username=user, password=key)
                stdin, _, _ = ssh.exec_command("echo 'SSH connection test'")
                transport = ssh.get_transport()
                sftp = paramiko.SFTPClient.from_transport(transport)

                remote_path = os.path.join(ephemeral_path,self.run_name,'RESULTS')
                remote_files = sftp.listdir_attr(remote_path)

                for file_attr in remote_files:
                    remote_file_path = os.path.join(remote_path, file_attr.filename)
                    local_file_path = os.path.join(self.save_path_runID, file_attr.filename)

                    # Check if it's a regular file before copying
                    if file_attr.st_mode & 0o100000:
                        sftp.get(remote_file_path, local_file_path)

                log.info('-' * 100)
                log.info(f'Files successfully copied at {self.save_path}')

                if stdin is not None:
                    break
                
            except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                if login == try_logins[-1]:
                    raise e
                else:
                    log.info(f'SSH connection failed with login {login}, trying again ...')
                    continue

            ### closing HPC session
            finally:
                if 'sftp' in locals():
                    sftp.close()
                if 'stdin' in locals():
                    stdin.close()
                if 'ssh' in locals():
                    ssh.close()
                    
            log.info('-' * 100)
            log.info('-' * 100)

    ### Post-processing function extracting relevant outputs from sim's final timestep.

    def post_process(self,log):

        ### Extracting Interfacial Area from CSV
        os.chdir(self.save_path_runID) 
        pvdfiles = glob.glob('VAR_*_time=*.pvd')
        maxpvd_tf = max(float(filename.split('=')[-1].split('.pvd')[0]) for filename in pvdfiles)

        df_csv = pd.read_csv(os.path.join(self.save_path_runID,f'{self.run_name}.csv'))
        df_csv['diff'] = abs(df_csv['Time']-maxpvd_tf)
        log.info('Reading data from csv')
        log.info('-'*100)

        tf_row = df_csv.sort_values(by='diff')

        IntA = tf_row.iloc[0]['INTERFACE_SURFACE_AREA']
        log.info('Interfacial area extracted')
        log.info('-'*100)

        os.chdir(self.local_path)

        ### Running pvpython script for Nd and DSD
        script_path = os.path.join(self.local_path,'PV_ndrop_DSD.py')

        log.info('Executing pvpython script')
        log.info('-'*100)

        try:
            output = subprocess.run(['pvpython', script_path, self.save_path , self.run_name], 
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            captured_stdout = output.stdout.decode('utf-8').strip().split('\n')
            outlines= []
            for i, line in enumerate(captured_stdout):
                stripline = line.strip()
                outlines.append(stripline)
                if i < len(captured_stdout) - 1:
                    log.info(stripline)
            
            df_DSD = pd.read_json(outlines[-1], orient='split', dtype=float, precise_float=True)


        except subprocess.CalledProcessError as e:
            log.info(f"Error executing the script with pvpython: {e}")
            df_DSD = None
        except FileNotFoundError:
            log.info("pvpython command not found. Make sure Paraview is installed and accessible in your environment.")
            df_DSD = None

        return df_DSD, IntA
