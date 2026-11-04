@echo off
setlocal EnableExtensions

if not defined MODEL_OPT_CONDA_EXE (
  echo ERROR: MODEL_OPT_CONDA_EXE is not set.
  exit /b 2
)
if not defined MODEL_OPT_CONDA_ENV set "MODEL_OPT_CONDA_ENV=Runner"
if not defined MODEL_OPT_MODEL_PATH (
  echo ERROR: MODEL_OPT_MODEL_PATH is not set.
  exit /b 2
)
if not defined MODEL_OPT_CALIBRATION_ROOT (
  echo ERROR: MODEL_OPT_CALIBRATION_ROOT is not set.
  exit /b 2
)
if not defined MODEL_OPT_OUTPUT_ROOT set "MODEL_OPT_OUTPUT_ROOT=%USERPROFILE%\Desktop\Raco_ModelOpt_Output"
if not defined MODEL_OPT_RESOLUTIONS set "MODEL_OPT_RESOLUTIONS=512,1024"
if not defined MODEL_OPT_MAX_PAIRS set "MODEL_OPT_MAX_PAIRS=24"
if not defined MODEL_OPT_CALIBRATION_EP set "MODEL_OPT_CALIBRATION_EP=cuda:0"
if not defined MODEL_OPT_COLOR_ORDER set "MODEL_OPT_COLOR_ORDER=RGB"
if not defined MODEL_OPT_PIXEL_DIVISOR set "MODEL_OPT_PIXEL_DIVISOR=255.0"
if not defined MODEL_OPT_INSTALL_IF_MISSING set "MODEL_OPT_INSTALL_IF_MISSING=true"
if not defined MODEL_OPT_MEAN set "MODEL_OPT_MEAN="
if not defined MODEL_OPT_STD set "MODEL_OPT_STD="

if not exist "%MODEL_OPT_CONDA_EXE%" (
  echo ERROR: Conda executable not found: %MODEL_OPT_CONDA_EXE%
  exit /b 2
)
if not exist "%MODEL_OPT_MODEL_PATH%" (
  echo ERROR: Source ONNX not found: %MODEL_OPT_MODEL_PATH%
  exit /b 2
)
if not exist "%MODEL_OPT_CALIBRATION_ROOT%\pairs.csv" (
  echo ERROR: pairs.csv not found: %MODEL_OPT_CALIBRATION_ROOT%\pairs.csv
  exit /b 2
)

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo ERROR: Visual Studio 2022 Build Tools were not found. Install MSVC v143 and a Windows SDK.
  exit /b 3
)

set "VSINSTALL_FILE=%TEMP%\modelopt_vsinstall.txt"
"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath > "%VSINSTALL_FILE%"
set /p VSINSTALL=<"%VSINSTALL_FILE%"
del "%VSINSTALL_FILE%" >nul 2>&1
if not defined VSINSTALL (
  echo ERROR: MSVC x64/x86 C++ Build Tools are missing.
  echo Install the Visual Studio 2022 workload: Desktop development with C++.
  exit /b 3
)

call "%VSINSTALL%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64
if errorlevel 1 (
  echo ERROR: VsDevCmd.bat failed.
  exit /b 3
)
where cl.exe
if errorlevel 1 (
  echo ERROR: cl.exe is not available after VsDevCmd.
  exit /b 3
)

nvidia-smi
if errorlevel 1 (
  echo ERROR: NVIDIA driver/GPU is not available to this self-hosted runner.
  exit /b 4
)

"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import modelopt.onnx.autocast, modelopt.onnx.quantization, onnx, onnxruntime, torch" >nul 2>&1
if errorlevel 1 (
  if /i not "%MODEL_OPT_INSTALL_IF_MISSING%"=="true" (
    echo ERROR: ModelOpt dependencies are missing in conda environment %MODEL_OPT_CONDA_ENV%.
    echo Enable install_if_missing or install nvidia-modelopt[onnx] manually.
    exit /b 5
  )
  echo Installing pinned NVIDIA ModelOpt ONNX dependencies into %MODEL_OPT_CONDA_ENV%...
  "%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -m pip install "nvidia-modelopt[onnx]==0.47.0"
  if errorlevel 1 exit /b 5
)

"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import cv2" >nul 2>&1
if errorlevel 1 (
  echo Installing OpenCV headless into %MODEL_OPT_CONDA_ENV%...
  "%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -m pip install opencv-python-headless
  if errorlevel 1 exit /b 5
)

"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import torch, onnxruntime as ort; print('Torch:', torch.__version__, 'CUDA:', torch.version.cuda); print('ORT providers:', ort.get_available_providers()); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
if errorlevel 1 exit /b 5

if /i "%MODEL_OPT_CALIBRATION_EP%"=="cuda:0" (
  "%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python -c "import onnxruntime as ort; assert 'CUDAExecutionProvider' in ort.get_available_providers(), 'CUDAExecutionProvider is missing'"
  if errorlevel 1 (
    echo ERROR: CUDAExecutionProvider is unavailable. Check CUDA 12.8, cuDNN 9.23 and onnxruntime-gpu.
    exit /b 5
  )
)

set "SCRIPT=%GITHUB_WORKSPACE%\scripts\modelopt_convert_raco_onnx.py"
"%MODEL_OPT_CONDA_EXE%" run -n "%MODEL_OPT_CONDA_ENV%" python "%SCRIPT%" ^
  --model "%MODEL_OPT_MODEL_PATH%" ^
  --calibration-root "%MODEL_OPT_CALIBRATION_ROOT%" ^
  --output-root "%MODEL_OPT_OUTPUT_ROOT%" ^
  --max-pairs "%MODEL_OPT_MAX_PAIRS%" ^
  --resolutions "%MODEL_OPT_RESOLUTIONS%" ^
  --calibration-ep "%MODEL_OPT_CALIBRATION_EP%" ^
  --color-order "%MODEL_OPT_COLOR_ORDER%" ^
  --pixel-divisor "%MODEL_OPT_PIXEL_DIVISOR%" ^
  --mean "%MODEL_OPT_MEAN%" ^
  --std "%MODEL_OPT_STD%"
if errorlevel 1 exit /b 6

exit /b 0
