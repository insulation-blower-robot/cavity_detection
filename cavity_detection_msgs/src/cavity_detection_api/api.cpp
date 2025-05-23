#include <ros/ros.h>
#include <cavity_detection_api/api.h>
#include <geometry_msgs/Pose.h>
#include <cavity_detection_msgs/GetNearestRoi.h>
#include <cavity_detection_msgs/UpdateRoi.h>
#include <cavity_detection_msgs/AddCavity.h>
#include <cavity_detection_msgs/UpdateCavity.h>
#include <cavity_detection_msgs/Roi.h>
#include <string>

bool getNearestRoi(cavity_detection_msgs::Roi& roi)
{
    ros::NodeHandle nh;
    ros::ServiceClient client = nh.serviceClient<cavity_detection_msgs::GetNearestRoi>("get_nearest_roi");
    cavity_detection_msgs::GetNearestRoi srv;

    if (client.call(srv))
    {
        std::string cavity_id = srv.response.roi.id;
        roi = srv.response.roi;


        ROS_INFO("Nearest cavity ID: %s", roi.id.c_str());
        return true;
    }
    else
    {
        ROS_ERROR("Service call failed");
        return false;
    }
}

bool updateRoi(const std::string roi_id, const geometry_msgs::Pose roi_pose, const double length, const double width, const double depth)
{
    ros::NodeHandle nh;
    ros::ServiceClient client = nh.serviceClient<cavity_detection_msgs::UpdateRoi>("update_roi");
    cavity_detection_msgs::UpdateRoi srv;
    srv.request.roi_id = roi_id;
    srv.request.roi_pose = roi_pose;
    srv.request.length = length;
    srv.request.width = width;
    srv.request.depth = depth;

    if (client.call(srv))
    {
        ROS_INFO("Update ROI success: %d", srv.response.success);
        return true;
    }
    else
    {
        ROS_ERROR("Service call failed");
        return false;
    }
}

bool addCavity(const std::string roi_id, const double y_offset, const double width, std::string& cavity_id)
{
    ros::NodeHandle nh;
    ros::ServiceClient client = nh.serviceClient<cavity_detection_msgs::AddCavity>("add_cavity");
    cavity_detection_msgs::AddCavity srv;
    srv.request.roi_id = roi_id;
    srv.request.y_offset = y_offset;
    srv.request.width = width;

    if (client.call(srv))
    {
        cavity_id = srv.response.cavity_id;
        ROS_INFO("Added cavity ID: %s, Success: %d", srv.response.cavity_id.c_str(), srv.response.success);
        return true;
    }
    else
    {
        ROS_ERROR("Service call failed");
        return false;
    }
}

bool updateCavity(const std::string roi_id, const std::string cavity_id, const double y_offset, const double width, const int status)
{
    ros::NodeHandle nh;
    ros::ServiceClient client = nh.serviceClient<cavity_detection_msgs::UpdateCavity>("update_cavity");
    cavity_detection_msgs::UpdateCavity srv;
    srv.request.roi_id = roi_id;
    srv.request.cavity_id = cavity_id;
    srv.request.y_offset = y_offset;
    srv.request.width = width;
    srv.request.status = status;

    if (client.call(srv))
    {
        ROS_INFO("Update cavity success: %d", srv.response.success);
        return true;
    }
    else
    {
        ROS_ERROR("Service call failed");
        return false;
    }
}

