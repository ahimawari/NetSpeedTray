using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using LibreHardwareMonitor.Hardware;

internal static class NetSpeedTrayLhmBridge
{
    private const int DefaultIntervalMs = 2000;
    private const string DefaultOutputPath = @"C:\ProgramData\NetSpeedTray\lhm-readings.json";
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);

    private static int Main(string[] args)
    {
        string outputPath = GetArg(args, "--output") ?? DefaultOutputPath;
        int intervalMs = ParseInterval(GetArg(args, "--interval-ms"));
        string directory = Path.GetDirectoryName(outputPath);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var computer = new Computer
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMotherboardEnabled = true,
            IsMemoryEnabled = true,
            IsStorageEnabled = true,
            IsControllerEnabled = true,
            IsPsuEnabled = true,
            IsBatteryEnabled = true
        };

        try
        {
            computer.Open();
            while (true)
            {
                try
                {
                    var readings = Poll(computer);
                    WriteReadings(outputPath, readings);
                }
                catch (Exception ex)
                {
                    WriteLog(outputPath, ex);
                }

                Thread.Sleep(intervalMs);
            }
        }
        finally
        {
            computer.Close();
        }
    }

    private static int ParseInterval(string value)
    {
        int intervalMs;
        if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out intervalMs) &&
            intervalMs >= 500 && intervalMs <= 60000)
        {
            return intervalMs;
        }

        return DefaultIntervalMs;
    }

    private static string GetArg(string[] args, string name)
    {
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
            {
                return args[i + 1];
            }
        }

        return null;
    }

    private static Readings Poll(Computer computer)
    {
        foreach (IHardware hardware in computer.Hardware)
        {
            Update(hardware);
        }

        var readings = new Readings();
        foreach (IHardware hardware in computer.Hardware)
        {
            Collect(hardware, readings);
        }

        return readings;
    }

    private static void Update(IHardware hardware)
    {
        hardware.Update();
        foreach (IHardware subHardware in hardware.SubHardware)
        {
            Update(subHardware);
        }
    }

    private static void Collect(IHardware hardware, Readings readings)
    {
        foreach (ISensor sensor in hardware.Sensors)
        {
            ConsiderSensor(hardware, sensor, readings);
        }

        foreach (IHardware subHardware in hardware.SubHardware)
        {
            Collect(subHardware, readings);
        }
    }

    private static void ConsiderSensor(IHardware hardware, ISensor sensor, Readings readings)
    {
        if (!sensor.Value.HasValue)
        {
            return;
        }

        double value = sensor.Value.Value;
        if (double.IsNaN(value) || double.IsInfinity(value))
        {
            return;
        }

        bool isCpu = hardware.HardwareType == HardwareType.Cpu;
        bool isGpu = hardware.HardwareType == HardwareType.GpuAmd ||
                     hardware.HardwareType == HardwareType.GpuIntel ||
                     hardware.HardwareType == HardwareType.GpuNvidia;
        string sensorName = (sensor.Name ?? string.Empty).ToLowerInvariant();

        if (sensor.SensorType == SensorType.Temperature && value > 0.0 && value < 150.0)
        {
            if (isCpu && !sensorName.Contains("distance"))
            {
                int priority = 10;
                if (sensorName.Contains("package"))
                {
                    priority = 100;
                }
                else if (sensorName.Contains("core max"))
                {
                    priority = 80;
                }
                else if (sensorName.Contains("core average"))
                {
                    priority = 70;
                }

                readings.SetCpuTemp(value, priority);
            }
            else if (isGpu)
            {
                int priority = 50;
                if (sensorName.Contains("gpu core"))
                {
                    priority = 100;
                }
                else if (sensorName.Contains("hot spot") || sensorName.Contains("junction"))
                {
                    priority = 20;
                }

                readings.SetGpuTemp(value, priority);
            }
        }
        else if (sensor.SensorType == SensorType.Power && value > 0.0 && value < 1000.0)
        {
            if (isCpu)
            {
                int priority = sensorName.Contains("package") ? 100 : 40;
                readings.SetCpuPower(value, priority);
            }
            else if (isGpu)
            {
                int priority = sensorName.Contains("package") || sensorName.Contains("power") ? 100 : 40;
                readings.SetGpuPower(value, priority);
            }
        }
    }

    private static void WriteReadings(string outputPath, Readings readings)
    {
        string json = readings.ToJson();
        string tempPath = outputPath + ".tmp";

        File.WriteAllText(tempPath, json, Utf8NoBom);
        if (File.Exists(outputPath))
        {
            try
            {
                File.Replace(tempPath, outputPath, null);
                return;
            }
            catch
            {
                File.Delete(outputPath);
            }
        }

        File.Move(tempPath, outputPath);
    }

    private static void WriteLog(string outputPath, Exception ex)
    {
        try
        {
            string logPath = Path.ChangeExtension(outputPath, ".log");
            string line = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + " " + ex + Environment.NewLine;
            File.AppendAllText(logPath, line, Utf8NoBom);
        }
        catch
        {
        }
    }

    private sealed class Readings
    {
        private int _cpuTempPriority = -1;
        private int _gpuTempPriority = -1;
        private int _cpuPowerPriority = -1;
        private int _gpuPowerPriority = -1;

        public double? CpuTemp { get; private set; }
        public double? GpuTemp { get; private set; }
        public double? CpuPower { get; private set; }
        public double? GpuPower { get; private set; }

        public void SetCpuTemp(double value, int priority)
        {
            if (priority > _cpuTempPriority)
            {
                CpuTemp = value;
                _cpuTempPriority = priority;
            }
        }

        public void SetGpuTemp(double value, int priority)
        {
            if (priority > _gpuTempPriority)
            {
                GpuTemp = value;
                _gpuTempPriority = priority;
            }
        }

        public void SetCpuPower(double value, int priority)
        {
            if (priority > _cpuPowerPriority)
            {
                CpuPower = value;
                _cpuPowerPriority = priority;
            }
        }

        public void SetGpuPower(double value, int priority)
        {
            if (priority > _gpuPowerPriority)
            {
                GpuPower = value;
                _gpuPowerPriority = priority;
            }
        }

        public string ToJson()
        {
            var parts = new List<string>
            {
                "\"version\":1",
                "\"source\":\"LibreHardwareMonitorLib\"",
                "\"timestamp\":" + UnixTimestamp().ToString("0.###", CultureInfo.InvariantCulture),
                "\"updated_utc\":\"" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + "\""
            };

            AddNumber(parts, "cpu_temp", CpuTemp);
            AddNumber(parts, "gpu_temp", GpuTemp);
            AddNumber(parts, "cpu_power", CpuPower);
            AddNumber(parts, "gpu_power", GpuPower);
            return "{" + string.Join(",", parts.ToArray()) + "}";
        }

        private static void AddNumber(List<string> parts, string key, double? value)
        {
            if (value.HasValue)
            {
                parts.Add("\"" + key + "\":" + value.Value.ToString("0.###", CultureInfo.InvariantCulture));
            }
        }

        private static double UnixTimestamp()
        {
            return (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
        }
    }
}
