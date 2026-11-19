# Logs estructurados por ambiente: /aegisvolt/<env>/<servicio>
# Métrica de errores por servicio (filtro JSON level=ERROR): la usa el AnalysisTemplate del canary.

resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(var.services)
  name              = "/aegisvolt/${var.environment}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each       = aws_cloudwatch_log_group.service
  name           = "${each.key}-errors"
  log_group_name = each.value.name
  pattern        = "{ $.level = \"ERROR\" }"
  metric_transformation {
    name          = "ErrorCount"
    namespace     = "AegisVolt/${var.environment}"
    value         = "1"
    default_value = "0"
    dimensions    = { Service = "$.service", Version = "$.version" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "errors_total" {
  for_each       = aws_cloudwatch_log_group.service
  name           = "${each.key}-errors-total"
  log_group_name = each.value.name
  pattern        = "{ $.level = \"ERROR\" }"
  metric_transformation {
    name          = "ErrorCountTotal"
    namespace     = "AegisVolt/${var.environment}"
    value         = "1"
    default_value = "0"
    dimensions    = { Service = "$.service" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "requests" {
  for_each       = aws_cloudwatch_log_group.service
  name           = "${each.key}-requests"
  log_group_name = each.value.name
  pattern        = "{ $.message = \"http\" }"
  metric_transformation {
    name          = "RequestCount"
    namespace     = "AegisVolt/${var.environment}"
    value         = "1"
    default_value = "0"
    dimensions    = { Service = "$.service", Version = "$.version" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "compensations" {
  name           = "saga-compensations"
  log_group_name = aws_cloudwatch_log_group.service["saga-orchestrator"].name
  pattern        = "{ $.to = \"COMPENSATED\" }"
  metric_transformation {
    name          = "SagasCompensated"
    namespace     = "AegisVolt/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each            = aws_cloudwatch_log_group.service
  alarm_name          = "aegisvolt-${var.environment}-${each.key}-errors"
  namespace           = "AegisVolt/${var.environment}"
  metric_name         = "ErrorCountTotal"
  dimensions          = { Service = each.key }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  threshold           = var.error_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

# Consulta guardada para rastrear una SAGA completa por correlation_id en todos los servicios.
resource "aws_cloudwatch_query_definition" "saga_trace" {
  name            = "AegisVolt/${var.environment}/trace-saga-by-correlation-id"
  log_group_names = [for g in aws_cloudwatch_log_group.service : g.name]

  query_string = <<-EOT
    fields @timestamp, service, level, message, `from`, `to`, step, step_status, reason
    | filter correlation_id = "REEMPLAZAR-CON-SAGA-ID"
    | sort @timestamp asc
  EOT
}
