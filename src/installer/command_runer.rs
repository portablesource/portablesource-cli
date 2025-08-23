// src/installer/command_runner.rs

use crate::{Result, PortableSourceError};
use crate::envs_manager::PortableEnvironmentManager;
use log::{info, debug};
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

// Enum для типизации команд. Он может остаться здесь.
#[derive(Clone, Copy, Debug)]
pub enum CommandType {
    Git,
    Pip,
    Uv,
    Python,
    Other,
}

/// CommandRunner - это централизованный исполнитель всех внешних команд.
/// Он держит ссылку на EnvironmentManager, чтобы правильно настраивать окружение.
pub struct CommandRunner<'a> {
    env_manager: &'a PortableEnvironmentManager,
}

impl<'a> CommandRunner<'a> {
    pub fn new(env_manager: &'a PortableEnvironmentManager) -> Self {
        Self { env_manager }
    }

    /// Публичный метод для запуска команды с выводом в лог.
    /// Это замена `run_tool_with_env`.
    pub fn run(&self, args: &[String], label: Option<&str>, cwd: Option<&Path>) -> Result<()> {
        if args.is_empty() { return Ok(()); }
        
        let mut cmd = self.create_command(args, cwd);
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        
        let command_type = self.determine_command_type(args);
        
        self.run_with_progress(cmd, label, command_type)
    }

    /// Публичный метод для "тихого" запуска.
    /// Это замена `run_tool_with_env_silent`.
    pub fn run_silent(&self, args: &[String], label: Option<&str>, cwd: Option<&Path>) -> Result<()> {
        if args.is_empty() { return Ok(()); }
        if let Some(l) = label { info!("{}...", l); }

        let mut cmd = self.create_command(args, cwd);
        cmd.stdout(Stdio::null()).stderr(Stdio::null());
        
        let status = cmd.status().map_err(|e| PortableSourceError::command(e.to_string()))?;
        if !status.success() {
            return Err(PortableSourceError::command(format!("Silent command failed with status: {}", status)));
        }
        Ok(())
    }

    // --- Приватные хелперы (логика из твоих старых функций) ---

    /// Создает объект `Command` с настроенным окружением.
    fn create_command(&self, args: &[String], cwd: Option<&Path>) -> Command {
        let mut cmd = Command::new(&args[0]);
        cmd.args(&args[1..]);
        if let Some(dir) = cwd {
            cmd.current_dir(dir);
        }
        let envs = self.env_manager.setup_environment_for_subprocess();
        cmd.envs(envs);
        
        // Hide console window on Windows
        #[cfg(windows)]
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        
        cmd
    }
    
    /// Определяет тип команды по аргументам.
    fn determine_command_type(&self, args: &[String]) -> CommandType {
        // Твоя логика определения типа команды...
        // ... (скопировано 1-в-1 из твоего run_tool_with_env)
        if args.len() >= 2 {
            let exe_name = Path::new(&args[0]).file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or(&args[0])
                .to_lowercase();
            
            if exe_name == "python" || exe_name == "python3" {
                if args.len() >= 3 && args[1] == "-m" {
                    match args[2].as_str() {
                        "pip" => CommandType::Pip,
                        "uv" => CommandType::Uv,
                        _ => CommandType::Python,
                    }
                } else {
                    CommandType::Python
                }
            } else if exe_name == "pip" || exe_name == "pip3" {
                CommandType::Pip
            } else if exe_name == "uv" {
                CommandType::Uv
            } else if exe_name == "git" {
                CommandType::Git
            } else {
                CommandType::Other
            }
        } else {
            CommandType::Other
        }
    }

    /// Основная логика выполнения команды с захватом stdout/stderr.
    /// Это замена `run_with_progress_typed`.
    fn run_with_progress(&self, mut cmd: Command, label: Option<&str>, command_type: CommandType) -> Result<()> {
        // Твоя логика выполнения...
        // ... (скопировано 1-в-1 из run_with_progress_typed)
        if let Some(l) = label { info!("{}...", l); }
        let mut child = cmd.spawn().map_err(|e| PortableSourceError::command(e.to_string()))?;
        
        let mut stderr_lines = Vec::new();
        
        let error_prefix = match command_type {
            CommandType::Git => "Git command failed",
            CommandType::Pip => "Pip command failed",
            CommandType::Uv => "UV command failed",
            CommandType::Python => "Python command failed",
            CommandType::Other => "Command failed",
        };
        
        if let Some(out) = child.stdout.take() {
            let reader = BufReader::new(out);
            for line in reader.lines().flatten() { debug!("[stdout] {}", line); }
        }
        
        if let Some(err) = child.stderr.take() {
            let reader = BufReader::new(err);
            for line in reader.lines().flatten() {
                debug!("[stderr] {}", line);
                stderr_lines.push(line);
            }
        }
        
        let status = child.wait().map_err(|e| PortableSourceError::command(e.to_string()))?;
        if !status.success() {
            let error_msg = if !stderr_lines.is_empty() {
                format!("Command failed with status: {}\nOutput:\n{}", status, stderr_lines.join("\n"))
            } else {
                format!("Command failed with status: {}", status)
            };
            debug!("{}: {}", error_prefix, error_msg);
            return Err(PortableSourceError::command(error_msg));
        }
        Ok(())
    }
}