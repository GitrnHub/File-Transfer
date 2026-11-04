@echo off
setlocal EnableExtensions
if not defined MODEL_OPT_CONDA_EXE exit /b 2
if not defined MODEL_OPT_CONDA_ENV set "MODEL_OPT_CONDA_ENV=Runner"
if not defined MODEL_OPT_MODEL_PATH exit /b 2
if not defined MODEL_OPT_CALIBRATION_ROOT exit /b 2
if not defined MODEL_OPT_OUTPUT_ROOT set "MODEL_OPT_OUTPUT_ROOT=%USERPROFILE%\Desktop\Raco_ModelOpt_Output"
if not defined MODEL_OPT_RESOLUTIONS set "MODEL_OPT_RESOLUTIONS=512,1024"
if not defined MODEL_OPT_MAX_PAIRS set "MODEL_OPT_MAX_PAIRS=24"
if not defined MODEL_OPT_CALIBRATION_EP set "MODEL_OPT_CALIBRATION_EP=cuda:0"
if not defined MODEL_OPT_COLOR_ORDER set "MODEL_OPT_COLOR_ORDER=RGB"
if not defined MODEL_OPT_PIXEL_DIVISOR set "MODEL_OPT_PIXEL_DIVISOR=255.0"
if not defined MODEL_OPT_MEAN set "MODEL_OPT_MEAN="
if not defined MODEL_OPT_STD set "MODEL_OPT_STD="

if not exist "%MODEL_OPT_CONDA_EXE%" exit /b 2
if not exist "%MODEL_OPT_MODEL_PATH%" exit /b 2
if not exist "%MODEL_OPT_CALIBRATION_ROOT%\pairs.csv" exit /b 2

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo ERROR: Install Visual Studio 2022 Build Tools with MSVC v143 and a Windows SDK.
  exit /b 3
)
set "VSINSTALL_FILE=%TEMP%\modelopt_vsinstall.txt"
"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "%VSINSTALL_FILE%"
set /p VSINSTALL=<"%VSINSTALL_FILE%"
del "%VSINSTALL_FILE%" >nul 2>&1
if not defined VSINSTALL exit /b 3
call "%VSINSTALL%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64
if errorlevel 1 exit /b 3
where cl.exe
if errorlevel 1 exit /b 3
nvidia-smi
if errorlevel 1 exit /b 4

"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import modelopt.onnx.autocast, modelopt.onnx.quantization, onnx, onnxruntime, torch, cv2"
if errorlevel 1 (
  echo ERROR: Required ModelOpt dependencies missing in Runner. Install nvidia-modelopt[onnx], onnxruntime-gpu and opencv-python-headless first.
  exit /b 5
)
"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import torch, onnxruntime as ort; print('Torch:',torch.__version__,'CUDA:',torch.version.cuda); print('ORT providers:',ort.get_available_providers()); assert torch.cuda.is_available()"
if errorlevel 1 exit /b 5
if /i "%MODEL_OPT_CALIBRATION_EP%"=="cuda:0" (
  "%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import onnxruntime as ort; assert 'CUDAExecutionProvider' in ort.get_available_providers()"
  if errorlevel 1 exit /b 5
)

set "SCRIPT=%GITHUB_WORKSPACE%\scripts\modelopt_convert_raco_onnx.py"
"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python "%SCRIPT%" --model "%MODEL_OPT_MODEL_PATH%" --calibration-root "%MODEL_OPT_CALIBRATION_ROOT%" --output-root "%MODEL_OPT_OUTPUT_ROOT%" --max-pairs "%MODEL_OPT_MAX_PAIRS%" --resolutions "%MODEL_OPT_RESOLUTIONS%" --calibration-ep "%MODEL_OPT_CALIBRATION_EP%" --color-order "%MODEL_OPT_COLOR_ORDER%" --pixel-divisor "%MODEL_OPT_PIXEL_DIVISOR%" --mean "%MODEL_OPT_MEAN%" --std "%MODEL_OPT_STD%"
exit /b %ERRORLEVEL%
